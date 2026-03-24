"""
Forced Alignment 테스트 — torchaudio MMS_FA 사용
원본 가사 + 보컬 음성 → 줄별 정확한 타이밍 → SRT + MP4

WhisperX transcribe 없이, 가사를 직접 음성에 정렬
"""
import os
import re
import sys
import subprocess
import torch
import torchaudio
import numpy as np

from google import genai
from moviepy.config import FFMPEG_BINARY

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
IMG_DIR = os.path.join(BASE_DIR, "06_img")
VIDEO_DIR = os.path.join(BASE_DIR, "07_Video")
SRT_DIR = os.path.join(BASE_DIR, "95_make_video_script", "srt")
VOCAL_DIR = os.path.join(BASE_DIR, "95_make_video_script", "vocals")
ENV_FILE = os.path.join(BASE_DIR, "92_make_image", ".env")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720


def safe_print(text):
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_lyrics(base_name):
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


def japanese_to_romaji(text):
    """일본어 텍스트를 로마자로 변환 (MMS_FA는 로마자/IPA 기반)"""
    import pykakasi
    kakasi = pykakasi.kakasi()
    result = kakasi.convert(text)
    romaji = ' '.join([item['hepburn'] for item in result])
    # MMS_FA 사전에 맞게 소문자, 알파벳만 유지
    romaji = re.sub(r'[^a-z\s]', '', romaji.lower())
    return romaji.strip()


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


def forced_align(vocal_path, lyrics, language='ja'):
    """torchaudio MMS_FA로 원본 가사를 음성에 강제 정렬"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # 모델 로드
    safe_print(f"  MMS_FA 모델 로드 (device: {device})...")
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model().to(device)
    LABELS = bundle.get_labels()
    DICTIONARY = bundle.get_dict()

    safe_print(f"  사전 크기: {len(DICTIONARY)}, 라벨: {len(LABELS)}")

    # 오디오 로드 (ffmpeg으로 16kHz mono PCM 변환 후 로드)
    decode_cmd = [
        FFMPEG_BINARY, '-i', vocal_path,
        '-f', 'f32le', '-ac', '1', '-ar', str(bundle.sample_rate),
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32)
    waveform = torch.from_numpy(audio_np.copy()).float().unsqueeze(0).to(device)

    duration = waveform.shape[1] / bundle.sample_rate
    safe_print(f"  오디오: {duration:.1f}초, 샘플레이트: {bundle.sample_rate}")

    # 가사를 로마자로 변환 후 토큰화
    entries = []
    for li, line in enumerate(lyrics):
        romaji = japanese_to_romaji(line)
        if not romaji.strip():
            safe_print(f"    줄{li+1} 로마자 변환 실패: {line[:30]}")
            entries.append({'text': line, 'start': 0, 'end': 0})
            continue

        # 토큰 시퀀스 생성
        tokens = []
        for char in romaji:
            if char == ' ':
                tokens.append(DICTIONARY.get('|', DICTIONARY.get(' ', None)))
            elif char in DICTIONARY:
                tokens.append(DICTIONARY[char])

        tokens = [t for t in tokens if t is not None]

        if not tokens:
            safe_print(f"    줄{li+1} 토큰 없음: {romaji[:30]}")
            entries.append({'text': line, 'start': 0, 'end': 0})
            continue

        entries.append({
            'text': line,
            'romaji': romaji,
            'tokens': tokens,
            'start': 0,
            'end': 0,
        })

    # 전체 가사를 하나의 토큰 시퀀스로 결합 (줄 경계 기록)
    all_tokens = []
    line_boundaries = []  # (start_token_idx, end_token_idx, entry_idx)
    STAR_TOKEN = DICTIONARY.get('*', DICTIONARY.get('|', 0))

    for i, entry in enumerate(entries):
        if 'tokens' not in entry:
            continue
        start_idx = len(all_tokens)
        all_tokens.extend(entry['tokens'])
        end_idx = len(all_tokens)
        line_boundaries.append((start_idx, end_idx, i))
        # 줄 사이 구분자
        if i < len(entries) - 1:
            all_tokens.append(STAR_TOKEN)

    safe_print(f"  총 토큰: {len(all_tokens)}, 줄 경계: {len(line_boundaries)}개")

    if not all_tokens:
        safe_print("  토큰 없음 — 정렬 불가")
        return entries

    # Emission 계산
    with torch.inference_mode():
        emission, _ = model(waveform)

    safe_print(f"  Emission shape: {emission.shape}")

    # CTC forced alignment
    TOKEN_IDS = torch.tensor([all_tokens], dtype=torch.int32, device=device)

    try:
        # torchaudio forced_align
        aligned_tokens, scores = torchaudio.functional.forced_align(
            emission, TOKEN_IDS, blank=0
        )
        aligned_tokens = aligned_tokens[0]  # batch dim 제거
        scores = scores[0]

        safe_print(f"  정렬 완료: {len(aligned_tokens)} 프레임")

        # 프레임 → 시간 변환
        frame_duration = waveform.shape[1] / emission.shape[1] / bundle.sample_rate

        # 토큰별 시간 추출 (non-blank만)
        token_times = []
        for frame_idx, (token_id, score) in enumerate(zip(aligned_tokens, scores)):
            if token_id.item() != 0:  # blank이 아닌 것만
                time_sec = frame_idx * frame_duration
                token_times.append({
                    'token_idx': len(token_times),
                    'time': time_sec,
                    'token_id': token_id.item(),
                    'score': score.item(),
                })

        safe_print(f"  Non-blank 토큰: {len(token_times)}개")

        # 줄 경계에서 시작/끝 시간 추출
        token_counter = 0
        for start_tok, end_tok, entry_idx in line_boundaries:
            num_tokens = end_tok - start_tok
            # 이 줄에 해당하는 token_times 찾기
            line_token_times = []
            for tt in token_times:
                # all_tokens에서의 인덱스와 매칭
                if start_tok <= token_counter + tt['token_idx'] < end_tok:
                    pass  # 아래에서 처리

            # 더 간단한 방법: 순차적으로 배분
            line_times = token_times[start_tok:end_tok] if end_tok <= len(token_times) else []

            if line_times:
                entries[entry_idx]['start'] = line_times[0]['time']
                entries[entry_idx]['end'] = line_times[-1]['time'] + frame_duration * 5
                avg_score = sum(t['score'] for t in line_times) / len(line_times)
                safe_print(f"    줄{entry_idx+1} {entries[entry_idx]['start']:>6.1f}~{entries[entry_idx]['end']:<6.1f} (score={avg_score:.2f}) {entries[entry_idx]['text'][:30]}")
            else:
                safe_print(f"    줄{entry_idx+1} 타이밍 없음: {entries[entry_idx]['text'][:30]}")

    except Exception as e:
        safe_print(f"  Forced alignment 오류: {e}")
        import traceback
        traceback.print_exc()

    return entries


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "04_Last_Train_Cassette_v1"
    language = sys.argv[2] if len(sys.argv) > 2 else "ja"

    base_name = target.rsplit('_v', 1)[0]
    version = 'v' + target.rsplit('_v', 1)[1]

    mp3_path = os.path.join(MP3_DIR, f"{target}.mp3")
    img_path = os.path.join(IMG_DIR, f"{base_name}_{version}.png")
    vocal_path = os.path.join(VOCAL_DIR, f"{target}_vocals.wav")
    srt_path = os.path.join(SRT_DIR, f"{target}_fa.srt")
    output_path = os.path.join(VIDEO_DIR, f"{target}_fa.mp4")

    if not os.path.exists(vocal_path):
        safe_print(f"보컬 파일 없음: {vocal_path}")
        safe_print("먼저 whisperX_only.py를 실행하여 보컬을 분리하세요.")
        return

    # 가사 추출
    lyrics = extract_lyrics(target)
    if not lyrics:
        safe_print("가사 없음")
        return
    safe_print(f"가사: {len(lyrics)}줄")

    # Forced Alignment
    entries = forced_align(vocal_path, lyrics, language)

    # 타이밍 없는 줄 보간
    for i, e in enumerate(entries):
        if e['start'] == 0 and e['end'] == 0:
            prev_end = 0
            next_start = None
            for j in range(i - 1, -1, -1):
                if entries[j]['end'] > 0:
                    prev_end = entries[j]['end']
                    break
            for j in range(i + 1, len(entries)):
                if entries[j]['start'] > 0:
                    next_start = entries[j]['start']
                    break
            if next_start and next_start > prev_end:
                gap = next_start - prev_end
                e['start'] = prev_end + gap * 0.2
                e['end'] = prev_end + gap * 0.8
                safe_print(f"    줄{i+1} 보간: {e['start']:.1f}~{e['end']:.1f}")

    # 후처리: 최소 duration + 순차
    for e in entries:
        if e['end'] - e['start'] < 2.5 and e['end'] > 0:
            e['end'] = e['start'] + 2.5
    for i in range(len(entries) - 1):
        if entries[i]['end'] > 0 and entries[i+1]['start'] > 0:
            if entries[i]['end'] > entries[i+1]['start']:
                entries[i]['end'] = entries[i+1]['start'] - 0.1

    # 번역
    original_lines = [e['text'] for e in entries]
    safe_print(f"\n  가사 번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)

    # SRT 저장
    os.makedirs(os.path.dirname(srt_path), exist_ok=True)
    with open(srt_path, 'w', encoding='utf-8') as f:
        idx = 1
        for i, e in enumerate(entries):
            if e['end'] - e['start'] < 0.3:
                continue
            f.write(f"{idx}\n")
            f.write(f"{format_srt_time(e['start'])} --> {format_srt_time(e['end'])}\n")
            f.write(f"{e['text']}\n")
            if translations and i < len(translations) and translations[i]:
                f.write(f"{translations[i]}\n")
            f.write(f"\n")
            idx += 1

    valid = len([e for e in entries if e['end'] - e['start'] >= 0.3])
    safe_print(f"  SRT 생성: {valid}개 세그먼트")

    # MP4 생성
    result = subprocess.run([FFMPEG_BINARY, '-i', mp3_path, '-f', 'null', '-'], capture_output=True, text=True)
    duration = None
    for line in result.stderr.split('\n'):
        if 'time=' in line:
            m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if m:
                duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    # 이퀄라이저 생성
    sys.path.insert(0, os.path.dirname(__file__))
    from video_with_lyrics import generate_circular_equalizer
    eq_path = os.path.join(SRT_DIR, f"{target}_fa_eq.mov")
    generate_circular_equalizer(mp3_path, eq_path, duration)

    srt_escaped = srt_path.replace('\\', '/').replace(':', '\\:')

    safe_print("  MP4 생성 중...")
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
        '-i', eq_path,
        '-filter_complex', filter_complex,
        '-map', '[out]', '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
        '-c:a', 'aac', '-b:a', '192k',
        '-t', str(duration), '-shortest', '-y',
        output_path,
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        safe_print(f"  ffmpeg 오류: {r.stderr[-500:]}")
        return

    size = os.path.getsize(output_path)
    safe_print(f"  완료: {output_path} ({size // (1024*1024)} MB)")


if __name__ == "__main__":
    main()
