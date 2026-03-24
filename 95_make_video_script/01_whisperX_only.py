"""
WhisperX 자막 생성 + 이퀄라이저 영상 생성 (해결과정.md 시도10 최종 버전)
1. Demucs htdemucs_6s 보컬 분리
2. WhisperX transcribe (VAD onset=0.3) → 세그먼트
3. WhisperX align → word-level 플랫 리스트
4. 줄 전체 텍스트 유사도 매칭 (슬라이딩 윈도우) → 가사 줄별 start/end 확정
5. 후처리 (최소 duration 2.5초, 순차 강제, 보간)
6. Gemini 이중언어 번역
7. 이미지 + mp3 + 이중언어 자막 + 원형 이퀄라이저 → mp4
- 인스트루멘탈 곡은 건너뜀

사용법:
    cd 95_make_video_script
    python 01_whisperX_only.py                              # 전체 보컬곡 처리
    python 01_whisperX_only.py 04_Last_Train_Cassette_v1    # 특정 곡만 처리
"""

import os
import re
import sys
import glob
import subprocess
import numpy as np
import torch
import whisperx

from difflib import SequenceMatcher
from google import genai
from moviepy import AudioFileClip
from moviepy.config import FFMPEG_BINARY

_ffmpeg_dir = os.path.dirname(FFMPEG_BINARY)
if _ffmpeg_dir not in os.environ.get('PATH', ''):
    os.environ['PATH'] = _ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

sys.path.insert(0, os.path.dirname(__file__))
from equalizer import generate_circular_equalizer

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
IMG_DIR = os.path.join(BASE_DIR, "06_img")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
VIDEO_DIR = os.path.join(BASE_DIR, "07_Video")
SRT_DIR = os.path.join(BASE_DIR, "05_Mp3")
TEMP_DIR = os.path.join(BASE_DIR, "95_make_video_script", "temp")
VOCAL_DIR = os.path.join(BASE_DIR, "95_make_video_script", "vocals")
ENV_FILE = os.path.join(BASE_DIR, "92_make_image", ".env")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
VIDEO_FPS = 24

MIN_DURATION = 2.5


def safe_print(text):
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


# ─── 가사/프롬프트 파싱 ───

def extract_lyrics_from_prompt(base_name):
    """가사만 추출 (섹션 태그 제외)"""
    prompt_name = re.sub(r'_v\d+$', '', base_name)
    prompt_path = os.path.join(PROMPT_DIR, f"{prompt_name}.md")
    if not os.path.exists(prompt_path):
        return []
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    if not lyrics_match:
        return []
    lines = []
    for line in lyrics_match.group(1).split('\n'):
        line = line.strip()
        if not line or re.match(r'^\[.*\]$', line):
            continue
        lines.append(line)
    return lines


def detect_song_info(prompt_filename):
    prompt_path = os.path.join(PROMPT_DIR, prompt_filename)
    if not os.path.exists(prompt_path):
        return None
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    style_match = re.search(r'## Style(?:\s+of\s+Music)?\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    style_text = style_match.group(1).strip() if style_match else ""

    is_instrumental = any(kw in style_text.lower() for kw in ['instrumental only', 'no vocals', 'no singing', 'no voice'])
    has_vocal_tag = any(kw in style_text for kw in ['Female Vocal', 'Male Vocal', 'Vocal', 'vocals', 'vocal', 'female', 'male'])
    if has_vocal_tag and 'no vocal' not in style_text.lower():
        is_instrumental = False

    if 'Korean' in style_text:
        language = 'ko'
    elif 'Japanese' in style_text:
        language = 'ja'
    else:
        lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
        lyrics_text = lyrics_match.group(1) if lyrics_match else ""
        if re.search(r'[가-힣]', lyrics_text):
            language = 'ko'
        elif re.search(r'[\u3040-\u309F\u30A0-\u30FF]', lyrics_text):
            language = 'ja'
        else:
            language = 'en'

    return {'is_instrumental': is_instrumental, 'language': language, 'style': style_text}


def find_vocal_songs():
    mp3_files = sorted(glob.glob(os.path.join(MP3_DIR, "*_v*.mp3")))
    songs = []
    for mp3_path in mp3_files:
        mp3_basename = os.path.splitext(os.path.basename(mp3_path))[0]
        version_match = re.match(r'^(.+)_(v\d+)$', mp3_basename)
        if not version_match:
            continue
        base_name = version_match.group(1)
        version = version_match.group(2)
        img_path = os.path.join(IMG_DIR, f"{base_name}_{version}.png")
        if not os.path.exists(img_path):
            continue
        info = detect_song_info(f"{base_name}.md")
        if info is None or info['is_instrumental']:
            continue
        songs.append({
            'name': f"{base_name}_{version}",
            'base_name': base_name,
            'version': version,
            'img': img_path,
            'mp3': mp3_path,
            'language': info['language'],
        })
    return songs


def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── Demucs 보컬 분리 ───

def load_demucs_model(device):
    from demucs.pretrained import get_model
    safe_print(f"  Demucs 모델 로딩 (htdemucs_6s, device: {device})...")
    model = get_model('htdemucs_6s').to(device)
    return model


def separate_vocals(mp3_path, target_name, demucs_model=None):
    os.makedirs(VOCAL_DIR, exist_ok=True)
    vocal_path = os.path.join(VOCAL_DIR, f"{target_name}_vocals.wav")
    if os.path.exists(vocal_path):
        safe_print(f"  보컬 캐시 존재 (재사용)")
        return vocal_path

    from demucs.apply import apply_model

    decode_cmd = [FFMPEG_BINARY, '-i', mp3_path, '-f', 'f32le', '-ac', '2', '-ar', '44100', '-v', 'quiet', '-']
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).T

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    safe_print(f"  demucs 보컬 분리 중... ({len(audio_np[0])/44100:.1f}초, device: {device})")

    if demucs_model is None:
        demucs_model = load_demucs_model(device)

    audio_tensor = torch.from_numpy(audio_np.copy()).float().unsqueeze(0).to(device)
    with torch.no_grad():
        sources = apply_model(demucs_model, audio_tensor, device=device, progress=True)
    vocal_idx = demucs_model.sources.index('vocals')
    vocals = sources[0, vocal_idx].cpu().numpy()

    del audio_tensor, sources
    torch.cuda.empty_cache()

    import wave
    vocals_int16 = (vocals.T * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(vocal_path, 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(vocals_int16.tobytes())
    safe_print(f"  보컬 분리 완료")
    return vocal_path


# ─── WhisperX 플랫 리스트 생성 ───

def generate_flat_list(vocal_path, language='en'):
    """WhisperX transcribe + align → word-level 플랫 리스트"""
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    safe_print(f"  WhisperX transcribe (device: {device}, onset=0.3)...")
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language,
                                 vad_options={"vad_onset": 0.3, "vad_offset": 0.21})
    audio = whisperx.load_audio(vocal_path)
    chunk_size = 10 if language == 'ja' else 30
    result = model.transcribe(audio, batch_size=16, language=language, chunk_size=chunk_size)

    safe_print(f"  WhisperX align (word-level)...")
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result['segments'], align_model, metadata, audio, device, return_char_alignments=False)

    flat_list = []
    for seg in result['segments']:
        for w in seg.get('words', []):
            word = w.get('word', '').strip()
            start = w.get('start')
            end = w.get('end')
            if word and start is not None:
                flat_list.append({
                    'word': word,
                    'start': float(start),
                    'end': float(end) if end is not None else float(start) + 0.2,
                })

    safe_print(f"  플랫 리스트: {len(flat_list)}개 단어")

    del model, align_model
    torch.cuda.empty_cache()

    return flat_list


# ─── 줄 전체 텍스트 유사도 매칭 (시도 10: 슬라이딩 윈도우) ───

def normalize_for_match(text, language):
    """매칭을 위한 정규화"""
    text = text.lower().strip()
    text = re.sub(r'[.,!?;:\'\"()\-–—]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if language == 'ja':
        # 카타카나 → 히라가나
        result = []
        for ch in text:
            cp = ord(ch)
            if 0x30A1 <= cp <= 0x30F6:
                result.append(chr(cp - 0x60))
            else:
                result.append(ch)
        text = ''.join(result)
    return text


def match_lines_sliding_window(flat_list, lyrics, language):
    """
    시도 10: 줄 전체 텍스트 유사도 슬라이딩 윈도우 매칭
    - 가사 줄 전체 텍스트를 WhisperX 연속 단어 구간과 비교
    - 줄 단어 수 ±2 범위로 윈도우 크기 탐색
    - 줄 단위 확정 후 해당 구간 소진, 다음 줄은 그 이후부터 탐색
    """
    if language == 'ja':
        # 일본어: 단어가 아닌 문자 단위이므로 다른 방식 필요
        return match_lines_japanese(flat_list, lyrics)

    assignments = []
    flat_idx = 0

    for line_num, line_text in enumerate(lyrics):
        line_words = line_text.split()
        n_words = len(line_words)
        line_norm = normalize_for_match(line_text, language)

        best_sim = 0
        best_start_idx = -1
        best_window_size = n_words

        # 슬라이딩 윈도우: 줄 단어 수 ±2 범위
        for window_size in range(max(1, n_words - 2), n_words + 3):
            search_end = min(flat_idx + window_size + 30, len(flat_list))
            for si in range(flat_idx, search_end - window_size + 1):
                window_words = [flat_list[si + j]['word'] for j in range(window_size)]
                window_text = normalize_for_match(' '.join(window_words), language)
                sim = SequenceMatcher(None, line_norm, window_text).ratio()

                if sim > best_sim:
                    best_sim = sim
                    best_start_idx = si
                    best_window_size = window_size

        if best_sim >= 0.4 and best_start_idx >= 0:
            start_time = flat_list[best_start_idx]['start']
            end_time = flat_list[best_start_idx + best_window_size - 1]['end']
            assignments.append({
                'start': start_time,
                'end': end_time,
                'text': line_text,
                'similarity': best_sim,
                'matched': True,
            })
            flat_idx = best_start_idx + best_window_size
            safe_print(f"    줄{line_num+1:2d} sim={best_sim:.2f} [{start_time:.1f}~{end_time:.1f}] {line_text[:40]}")
        else:
            assignments.append({
                'start': None,
                'end': None,
                'text': line_text,
                'similarity': best_sim,
                'matched': False,
            })
            safe_print(f"    줄{line_num+1:2d} 매칭 실패 (best_sim={best_sim:.2f}) {line_text[:40]}")

    return assignments


def match_lines_japanese(flat_list, lyrics):
    """일본어: 문자 단위 플랫 리스트 → 줄 단위 매칭"""
    assignments = []
    flat_idx = 0

    for line_num, line_text in enumerate(lyrics):
        line_chars = [ch for ch in line_text if ch.strip()]
        n_chars = len(line_chars)
        line_norm = normalize_for_match(''.join(line_chars), 'ja')

        best_sim = 0
        best_start_idx = -1
        best_window_size = n_chars

        for window_size in range(max(1, n_chars - 3), n_chars + 4):
            search_end = min(flat_idx + window_size + 50, len(flat_list))
            for si in range(flat_idx, search_end - window_size + 1):
                window_chars = [flat_list[si + j]['word'] for j in range(window_size)]
                window_text = normalize_for_match(''.join(window_chars), 'ja')
                sim = SequenceMatcher(None, line_norm, window_text).ratio()

                if sim > best_sim:
                    best_sim = sim
                    best_start_idx = si
                    best_window_size = window_size

        if best_sim >= 0.35 and best_start_idx >= 0:
            start_time = flat_list[best_start_idx]['start']
            end_time = flat_list[best_start_idx + best_window_size - 1]['end']
            assignments.append({
                'start': start_time,
                'end': end_time,
                'text': line_text,
                'similarity': best_sim,
                'matched': True,
            })
            flat_idx = best_start_idx + best_window_size
            safe_print(f"    줄{line_num+1:2d} sim={best_sim:.2f} [{start_time:.1f}~{end_time:.1f}] {line_text[:30]}")
        else:
            assignments.append({
                'start': None,
                'end': None,
                'text': line_text,
                'similarity': best_sim,
                'matched': False,
            })
            safe_print(f"    줄{line_num+1:2d} 매칭 실패 (best_sim={best_sim:.2f}) {line_text[:30]}")

    return assignments


# ─── 후처리 ───

def postprocess_assignments(assignments):
    """
    후처리:
    - 매칭 실패 줄은 앞뒤 기준 보간
    - 최소 duration 2.5초 강제
    - 순차 강제 (다음 줄 start > 현재 줄 end)
    """
    # 1. 매칭 실패 줄 보간
    for i, a in enumerate(assignments):
        if a['start'] is not None:
            continue

        prev_end = None
        next_start = None

        for j in range(i - 1, -1, -1):
            if assignments[j]['end'] is not None:
                prev_end = assignments[j]['end']
                break

        for j in range(i + 1, len(assignments)):
            if assignments[j]['start'] is not None:
                next_start = assignments[j]['start']
                # 사이에 몇 줄이 보간 대상인지 계산
                gap_lines = j - (i - 1 if prev_end else i)
                break

        if prev_end is not None and next_start is not None:
            gap = next_start - prev_end
            # 보간 대상 줄들의 인덱스 범위
            first_missing = i
            for k in range(i - 1, -1, -1):
                if assignments[k]['start'] is not None:
                    first_missing = k + 1
                    break
            last_missing = i
            for k in range(i + 1, len(assignments)):
                if assignments[k]['start'] is not None:
                    last_missing = k - 1
                    break

            n_missing = last_missing - first_missing + 1
            if n_missing > 0:
                per_line = gap / n_missing
                idx_in_gap = i - first_missing
                a['start'] = prev_end + idx_in_gap * per_line
                a['end'] = prev_end + (idx_in_gap + 1) * per_line
                a['matched'] = False
                safe_print(f"    보간: 줄{i+1} [{a['start']:.1f}~{a['end']:.1f}]")
        elif prev_end is not None:
            a['start'] = prev_end
            a['end'] = prev_end + 3.0
            a['matched'] = False
        elif next_start is not None:
            a['start'] = max(0, next_start - 3.0)
            a['end'] = next_start
            a['matched'] = False

    # 2. 최소 duration 강제
    for a in assignments:
        if a['start'] is not None and a['end'] is not None:
            if a['end'] - a['start'] < MIN_DURATION:
                a['end'] = a['start'] + MIN_DURATION

    # 3. 순차 강제 (다음 줄 start > 현재 줄 end)
    for i in range(len(assignments) - 1):
        if assignments[i]['end'] is not None and assignments[i + 1]['start'] is not None:
            if assignments[i]['end'] > assignments[i + 1]['start']:
                assignments[i]['end'] = assignments[i + 1]['start'] - 0.05

    return assignments


# ─── Gemini 번역 ───

def load_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def translate_lyrics(lines, source_lang):
    api_key = load_api_key()
    if not api_key:
        safe_print("  Gemini API 키 없음 — 번역 건너뜀")
        return None

    if source_lang == 'ko':
        instruction = "Translate each Korean lyrics line to natural English."
    elif source_lang == 'ja':
        instruction = "Translate each Japanese lyrics line to natural Korean."
    else:
        instruction = "Translate each English lyrics line to natural Korean."

    numbered = "\n".join(f"{i+1}. {line}" for i, line in enumerate(lines))
    prompt = (
        f"{instruction}\n"
        f"Return ONLY the translations, one per line, numbered to match.\n"
        f"Keep the same number of lines. Do not add explanations.\n\n"
        f"{numbered}"
    )
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        translated = []
        for line in response.text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            translated.append(re.sub(r'^\d+[\.\)]\s*', '', line))
        while len(translated) < len(lines):
            translated.append("")
        return translated[:len(lines)]
    except Exception as e:
        safe_print(f"  번역 오류: {e}")
        return None


# ─── SRT 생성 ───

def generate_srt(mp3_path, srt_path, target_name, language='en', demucs_model=None):
    """WhisperX 플랫 리스트 + 줄 유사도 매칭으로 SRT 생성"""
    lyrics = extract_lyrics_from_prompt(target_name)
    if not lyrics:
        safe_print(f"  가사 없음: {target_name}")
        return False

    if os.path.exists(srt_path):
        safe_print(f"  SRT 이미 존재: {os.path.basename(srt_path)} (재사용)")
        return True

    safe_print(f"  가사 {len(lyrics)}줄 (언어: {language})")

    # 1. 보컬 분리
    vocal_path = separate_vocals(mp3_path, target_name, demucs_model=demucs_model)

    # 2-3. WhisperX → 플랫 리스트
    flat_list = generate_flat_list(vocal_path, language)

    # 4. 줄 전체 텍스트 유사도 슬라이딩 윈도우 매칭
    safe_print(f"  줄 유사도 매칭 중...")
    assignments = match_lines_sliding_window(flat_list, lyrics, language)

    matched = sum(1 for a in assignments if a['matched'])
    safe_print(f"  매칭 결과: {matched}/{len(lyrics)}줄 성공")

    # 5. 후처리
    assignments = postprocess_assignments(assignments)

    # 6. 번역
    original_lines = [a['text'] for a in assignments]
    safe_print(f"  가사 번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)

    # 7. SRT 저장
    os.makedirs(os.path.dirname(srt_path), exist_ok=True)
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, a in enumerate(assignments):
            if a['start'] is None or a['end'] is None:
                continue
            if a['end'] - a['start'] < 0.3:
                continue
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(a['start'])} --> {format_srt_time(a['end'])}\n")
            f.write(f"{a['text']}\n")
            if translations and i < len(translations) and translations[i]:
                f.write(f"{translations[i]}\n")
            f.write(f"\n")

    valid = len([a for a in assignments if a['start'] is not None and a['end'] - a['start'] >= 0.3])
    safe_print(f"  SRT 생성 완료: {valid}개 세그먼트")
    return True


# ─── 영상 생성 ───

def get_audio_duration(mp3_path):
    result = subprocess.run([FFMPEG_BINARY, '-i', mp3_path, '-f', 'null', '-'], capture_output=True, text=True)
    for line in result.stderr.split('\n'):
        if 'time=' in line:
            m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


def create_video_with_srt(song, srt_path):
    """이미지 + mp3 + SRT 자막 + 이퀄라이저 → mp4"""
    img_path = song['img']
    mp3_path = song['mp3']
    output_path = os.path.join(VIDEO_DIR, f"{song['name']}.mp4")

    if os.path.exists(output_path):
        safe_print(f"  이미 존재: {output_path} (건너뜁니다)")
        return output_path

    duration = get_audio_duration(mp3_path)
    if duration is None:
        audio = AudioFileClip(mp3_path)
        duration = audio.duration
        audio.close()

    safe_print(f"  오디오 길이: {duration:.1f}초 ({duration/60:.1f}분)")

    os.makedirs(TEMP_DIR, exist_ok=True)
    eq_video_path = os.path.join(TEMP_DIR, f"{song['name']}_eq.mov")
    generate_circular_equalizer(mp3_path, eq_video_path, duration)

    srt_escaped = srt_path.replace('\\', '/').replace(':', '\\:')

    safe_print("  mp4 생성 중 (자막 + 이퀄라이저)...")
    filter_complex = (
        f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2[bg];"
        f"[bg][2:v]overlay=(W-w)/2:(H-h)/2,"
        f"subtitles='{srt_escaped}':force_style='"
        f"FontName=Malgun Gothic Bold,"
        f"FontSize=26,"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"BackColour=&H80000000,"
        f"BorderStyle=4,"
        f"Outline=1,"
        f"Shadow=0,"
        f"MarginV=40,"
        f"Alignment=2"
        f"'[out]"
    )
    cmd = [
        FFMPEG_BINARY,
        '-loop', '1', '-i', img_path,
        '-i', mp3_path,
        '-i', eq_video_path,
        '-filter_complex', filter_complex,
        '-map', '[out]', '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '192k',
        '-t', str(duration), '-shortest', '-y',
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        safe_print(f"  ffmpeg 오류: {result.stderr[-500:]}")
        return None

    file_size = os.path.getsize(output_path)
    safe_print(f"  저장 완료: {output_path} ({file_size // (1024*1024)} MB)")
    return output_path


# ─── Main ───

def main():
    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(SRT_DIR, exist_ok=True)

    target = sys.argv[1] if len(sys.argv) > 1 else None

    if target:
        songs = find_vocal_songs()
        song = next((s for s in songs if s['name'] == target), None)
        if not song:
            safe_print(f"곡을 찾을 수 없음: {target}")
            return
        songs = [song]
    else:
        songs = find_vocal_songs()

    if not songs:
        safe_print("보컬 곡이 없습니다.")
        return

    pending = []
    for song in songs:
        output_path = os.path.join(VIDEO_DIR, f"{song['name']}.mp4")
        if not os.path.exists(output_path):
            pending.append(song)

    safe_print(f"\n보컬 곡 목록 ({len(songs)}개, 처리 대상: {len(pending)}개):")
    for i, song in enumerate(songs):
        marker = " *" if song in pending else ""
        safe_print(f"  [{i}] {song['name']} ({song['language']}){marker}")

    if not pending:
        safe_print("\n처리할 곡이 없습니다 (모두 완료).")
        return

    # Demucs 모델 1회 로딩
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    needs_vocal = any(
        not os.path.exists(os.path.join(VOCAL_DIR, f"{s['name']}_vocals.wav"))
        for s in pending
    )
    demucs_model = load_demucs_model(device) if needs_vocal else None

    for song in pending:
        safe_print(f"\n처리: {song['name']}")

        try:
            srt_path = os.path.join(SRT_DIR, f"{song['name']}.srt")
            success = generate_srt(
                song['mp3'], srt_path, song['name'], song['language'],
                demucs_model=demucs_model,
            )
            if not success:
                safe_print(f"  SRT 생성 실패")
                continue
            create_video_with_srt(song, srt_path)
        except Exception as e:
            safe_print(f"  오류: {e}")
            import traceback
            traceback.print_exc()

    safe_print("\n모든 가사 영상 생성 완료!")


if __name__ == "__main__":
    main()
