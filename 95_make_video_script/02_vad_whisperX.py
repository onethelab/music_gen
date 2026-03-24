"""
VAD 하이브리드 자막 생성 + 이퀄라이저 영상 생성
1. Demucs 보컬 분리
2. pyannote VAD → 보컬 구간 감지
3. 곡 구조([Verse], [Chorus] 등) 기반 섹션 단위 가사 배분
4. 구간 내 균등 분배 → WhisperX word-level 미세조정 (±2초 tolerance)
5. Gemini 이중언어 번역
6. 이미지 + mp3 + 이중언어 자막 + 원형 이퀄라이저 → mp4
- 인스트루멘탈 곡은 건너뜀

사용법:
    cd 95_make_video_script
    python whisperX_only.py                              # 전체 보컬곡 처리
    python whisperX_only.py 04_Last_Train_Cassette_v1    # 특정 곡만 처리
"""

import os
import re
import sys
import glob
import subprocess
import numpy as np
import torch
import whisperx

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
SRT_DIR = os.path.join(BASE_DIR, "95_make_video_script", "srt")
VOCAL_DIR = os.path.join(BASE_DIR, "95_make_video_script", "vocals")
ENV_FILE = os.path.join(BASE_DIR, "92_make_image", ".env")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
VIDEO_FPS = 24


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


def extract_sections(base_name):
    """가사를 섹션 단위로 구조화 추출"""
    prompt_name = re.sub(r'_v\d+$', '', base_name)
    prompt_path = os.path.join(PROMPT_DIR, f"{prompt_name}.md")
    if not os.path.exists(prompt_path):
        return []
    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    if not lyrics_match:
        return []

    sections = []
    current_tag = ''
    current_lines = []

    for line in lyrics_match.group(1).split('\n'):
        line = line.strip()
        if re.match(r'^\[.*\]$', line):
            if current_tag or current_lines:
                sections.append({
                    'tag': current_tag,
                    'lines': current_lines,
                    'has_lyrics': len(current_lines) > 0,
                })
            current_tag = line
            current_lines = []
        elif line:
            current_lines.append(line)

    if current_tag or current_lines:
        sections.append({
            'tag': current_tag,
            'lines': current_lines,
            'has_lyrics': len(current_lines) > 0,
        })

    return sections


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


# ─── VAD 보컬 구간 감지 ───

def detect_vocal_segments(vocal_path):
    """pyannote VAD로 보컬 구간 감지"""
    safe_print("  VAD 감지 중 (pyannote)...")

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    decode_cmd = [FFMPEG_BINARY, '-i', vocal_path, '-f', 'f32le', '-ac', '1', '-ar', '16000', '-v', 'quiet', '-']
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32)
    waveform = torch.from_numpy(audio_np.copy()).float().unsqueeze(0)

    safe_print(f"  오디오: {len(audio_np)/16000:.1f}초")

    from pyannote.audio import Model
    from pyannote.audio.pipelines import VoiceActivityDetection

    model_fp = os.path.join(os.path.dirname(whisperx.__file__), "assets", "pytorch_model.bin")
    vad_model = Model.from_pretrained(model_fp)
    vad_model = vad_model.to(device)

    pipeline = VoiceActivityDetection(segmentation=vad_model)
    pipeline.instantiate({
        "onset": 0.25,
        "offset": 0.20,
        "min_duration_on": 0.5,
        "min_duration_off": 0.5,
    })

    vad_result = pipeline({"waveform": waveform, "sample_rate": 16000})

    segments = []
    for speech_turn, _, _ in vad_result.itertracks(yield_label=True):
        start = speech_turn.start
        end = speech_turn.end
        if end - start >= 1.5:
            segments.append([start, end])

    # 인접 구간 병합 (간격 기준 자동 조정)
    # 먼저 넓은 기준(4초)으로 병합 → 결과가 너무 적으면 더 넓게
    for merge_gap in [4.0, 6.0, 8.0]:
        merged = []
        for seg in segments:
            if merged and seg[0] - merged[-1][1] < merge_gap:
                merged[-1][1] = seg[1]
            else:
                merged.append(seg)
        # 의미있는 구간 수가 충분하면 종료 (3개 이상)
        usable = [m for m in merged if m[1] - m[0] >= 5.0]
        if len(usable) >= 3:
            break

    safe_print(f"  보컬 구간: {len(merged)}개 (병합 기준: {merge_gap}초)")
    for i, (s, e) in enumerate(merged):
        safe_print(f"    [{i}] {s:.1f}~{e:.1f} ({e-s:.1f}초)")

    del vad_model, pipeline
    torch.cuda.empty_cache()

    return merged


# ─── 곡 구조 기반 가사 배분 ───

def distribute_lyrics(segments, lyrics, sections):
    """섹션 단위로 보컬 구간에 매핑, 구간 내 균등 배치"""

    lyric_sections = [s for s in sections if s['has_lyrics']]

    # 인트로/아웃트로 보컬 구간 제거:
    # 첫 가사 섹션 이전의 무가사 섹션이 있으면, 그 시간대의 VAD 구간은 스킵
    # 마지막 가사 섹션 이후도 동일
    first_lyric_idx = next((i for i, s in enumerate(sections) if s['has_lyrics']), 0)
    last_lyric_idx = next((i for i in range(len(sections) - 1, -1, -1) if sections[i]['has_lyrics']), len(sections) - 1)

    has_intro = first_lyric_idx > 0  # [Intro] 등 무가사 섹션이 앞에 있음
    has_outro = last_lyric_idx < len(sections) - 1  # [Outro] 등 무가사 섹션이 뒤에 있음

    if has_intro and len(segments) > 1:
        # 첫 번째 보컬 구간이 인트로일 가능성 → 첫 구간 제거
        intro_tags = [sections[i]['tag'] for i in range(first_lyric_idx)]
        safe_print(f"  인트로 감지: {intro_tags} → 첫 VAD 구간 [{segments[0][0]:.1f}~{segments[0][1]:.1f}] 스킵")
        segments = segments[1:]

    if has_outro and len(segments) > 1:
        # 마지막 보컬 구간이 아웃트로일 가능성 → 마지막 구간 제거
        outro_tags = [sections[i]['tag'] for i in range(last_lyric_idx + 1, len(sections))]
        safe_print(f"  아웃트로 감지: {outro_tags} → 마지막 VAD 구간 [{segments[-1][0]:.1f}~{segments[-1][1]:.1f}] 스킵")
        segments = segments[:-1]

    total_vocal_time = sum(e - s for s, e in segments)
    total_lines = sum(len(s['lines']) for s in lyric_sections)
    avg_time_per_line = total_vocal_time / total_lines if total_lines > 0 else 3.0

    safe_print(f"  평균 줄당 시간: {avg_time_per_line:.1f}초")

    seg_capacity = []
    for s, e in segments:
        cap = max(1, round((e - s) / avg_time_per_line))
        seg_capacity.append(cap)

    seg_lines = {i: [] for i in range(len(segments))}
    seg_tags = {i: [] for i in range(len(segments))}
    seg_idx = 0
    current_count = 0

    for sec in lyric_sections:
        sec_size = len(sec['lines'])

        if seg_idx >= len(segments):
            seg_lines[len(segments) - 1].extend(sec['lines'])
            seg_tags[len(segments) - 1].append(sec['tag'])
            continue

        effective_cap = int(seg_capacity[seg_idx] * 1.5)
        remaining = effective_cap - current_count
        if remaining < sec_size and current_count > 0:
            seg_idx += 1
            current_count = 0
            while seg_idx < len(segments) and int(seg_capacity[seg_idx] * 1.5) < sec_size:
                seg_idx += 1

        if seg_idx >= len(segments):
            seg_lines[len(segments) - 1].extend(sec['lines'])
            seg_tags[len(segments) - 1].append(sec['tag'])
            continue

        # 현재 구간이 이 섹션보다 너무 작으면 건너뛰기 (50% 여유 포함)
        while seg_idx < len(segments) and int(seg_capacity[seg_idx] * 1.5) < sec_size and len(seg_lines[seg_idx]) == 0:
            safe_print(f"    구간{seg_idx} 건너뜀 (용량={seg_capacity[seg_idx]} < 섹션={sec_size}줄)")
            seg_idx += 1

        if seg_idx >= len(segments):
            seg_lines[len(segments) - 1].extend(sec['lines'])
            seg_tags[len(segments) - 1].append(sec['tag'])
            continue

        seg_lines[seg_idx].extend(sec['lines'])
        seg_tags[seg_idx].append(sec['tag'])
        current_count += sec_size

        if current_count >= seg_capacity[seg_idx]:
            seg_idx += 1
            current_count = 0

    safe_print(f"  구간별 배분:")
    for i, (s, e) in enumerate(segments):
        lines = seg_lines.get(i, [])
        tags = seg_tags.get(i, [])
        tag_str = ', '.join(tags) if tags else '(없음)'
        safe_print(f"    구간{i} [{s:.1f}~{e:.1f}] 용량={seg_capacity[i]} → {len(lines)}줄 {tag_str}")

    assignments = []
    for seg_idx, (seg_start, seg_end) in enumerate(segments):
        lines = seg_lines.get(seg_idx, [])
        if not lines:
            continue

        seg_duration = seg_end - seg_start
        time_per_line = seg_duration / len(lines)

        for i, line_text in enumerate(lines):
            line_start = seg_start + i * time_per_line
            line_end = seg_start + (i + 1) * time_per_line
            assignments.append({
                'start': line_start,
                'end': line_end,
                'text': line_text,
            })

    return assignments


# ─── WhisperX 미세조정 ───

def whisperx_fine_tune(assignments, vocal_path, language='en', tolerance=2.0):
    """WhisperX word-level 타이밍으로 균등 배분 결과를 미세조정
    ±tolerance 이내에 유사 단어가 있으면 시작 시간 조정, 없으면 균등 배분 유지
    """
    from difflib import SequenceMatcher

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    safe_print(f"  WhisperX 미세조정 (tolerance: ±{tolerance}초)...")

    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language,
                                 vad_options={"vad_onset": 0.3, "vad_offset": 0.21})
    audio = whisperx.load_audio(vocal_path)
    chunk_size = 10 if language == 'ja' else 30
    result = model.transcribe(audio, batch_size=16, language=language, chunk_size=chunk_size)

    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result['segments'], align_model, metadata, audio, device, return_char_alignments=False)

    all_words = []
    for seg in result['segments']:
        for w in seg.get('words', []):
            word = w.get('word', '').strip()
            start = w.get('start')
            if word and start is not None:
                all_words.append({'word': word, 'start': float(start)})

    safe_print(f"  WhisperX 단어: {len(all_words)}개")

    # 일본어 정규화
    _kakasi = None
    def normalize(text):
        nonlocal _kakasi
        if language != 'ja':
            return text.lower().strip(".,!?;:'\"")
        if _kakasi is None:
            import pykakasi
            _kakasi = pykakasi.kakasi()
        r = _kakasi.convert(text)
        return ''.join([item['hira'] for item in r]).strip()

    adjusted = 0
    kept = 0

    for a in assignments:
        baseline_start = a['start']
        first_words = a['text'].split()[:3]
        first_normalized = normalize(' '.join(first_words))

        best_match_time = None
        best_score = 0

        for w in all_words:
            if abs(w['start'] - baseline_start) > tolerance:
                continue

            w_normalized = normalize(w['word'])
            score = SequenceMatcher(None, first_normalized[:len(w_normalized)*2], w_normalized).ratio()

            if score > best_score and score >= 0.4:
                best_score = score
                best_match_time = w['start']

        if best_match_time is not None:
            diff = best_match_time - baseline_start
            a['start'] = best_match_time
            a['end'] = a['end'] + diff
            adjusted += 1
        else:
            kept += 1

    # 순서 보정
    for i in range(len(assignments) - 1):
        if assignments[i]['end'] > assignments[i+1]['start']:
            assignments[i]['end'] = assignments[i+1]['start'] - 0.05

    safe_print(f"  미세조정: {adjusted}줄 조정, {kept}줄 유지")

    del model, align_model
    torch.cuda.empty_cache()

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


# ─── SRT 생성 (VAD 하이브리드) ───

def generate_srt(mp3_path, srt_path, target_name, language='en', demucs_model=None):
    """VAD 구조 배분 + WhisperX 미세조정으로 SRT 생성"""
    lyrics = extract_lyrics_from_prompt(target_name)
    sections = extract_sections(target_name)
    if not lyrics:
        safe_print(f"  가사 없음: {target_name}")
        return False

    if os.path.exists(srt_path):
        safe_print(f"  SRT 이미 존재: {os.path.basename(srt_path)} (재사용)")
        return True

    safe_print(f"  가사 {len(lyrics)}줄, 섹션 {len(sections)}개 (언어: {language})")

    # 1. 보컬 분리
    vocal_path = separate_vocals(mp3_path, target_name, demucs_model=demucs_model)

    # 2. VAD 보컬 구간 감지
    segments = detect_vocal_segments(vocal_path)
    if not segments:
        safe_print("  보컬 구간 없음")
        return False

    # 3. 구조 기반 가사 배분 (균등)
    assignments = distribute_lyrics(segments, lyrics, sections)

    # 4. WhisperX 미세조정
    assignments = whisperx_fine_tune(assignments, vocal_path, language)

    # 5. 번역
    original_lines = [a['text'] for a in assignments]
    safe_print(f"  가사 번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)

    # 6. SRT 저장
    os.makedirs(os.path.dirname(srt_path), exist_ok=True)
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, a in enumerate(assignments):
            if a['end'] - a['start'] < 0.3:
                continue
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(a['start'])} --> {format_srt_time(a['end'])}\n")
            f.write(f"{a['text']}\n")
            if translations and i < len(translations) and translations[i]:
                f.write(f"{translations[i]}\n")
            f.write(f"\n")

    valid = len([a for a in assignments if a['end'] - a['start'] >= 0.3])
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

    eq_video_path = os.path.join(SRT_DIR, f"{song['name']}_eq.mov")
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
