"""
가사 포함 영상 생성 자동화
- Whisper로 mp3에서 가사 타이밍 추출 → SRT 생성
- Gemini로 가사 번역 (한→영, 영→한)
- 이미지 + mp3 + 이중언어 자막 → mp4
- 인스트루멘탈 곡은 건너뜀 (93_make_video/video_create.py 사용)

사용법:
    cd 95_make_video_script
    python video_with_lyrics.py
"""

import os
import re
import glob
import subprocess
from difflib import SequenceMatcher

from google import genai
from moviepy import ImageClip, AudioFileClip, CompositeVideoClip, TextClip
from moviepy.config import FFMPEG_BINARY

# Whisper가 ffmpeg을 찾을 수 있도록 PATH에 추가
_ffmpeg_dir = os.path.dirname(FFMPEG_BINARY)
if _ffmpeg_dir not in os.environ.get('PATH', ''):
    os.environ['PATH'] = _ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

import whisper

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "04_Suno_Prompt")
IMG_DIR = os.path.join(BASE_DIR, "06_img")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
VIDEO_DIR = os.path.join(BASE_DIR, "07_Video")
SRT_DIR = os.path.join(BASE_DIR, "95_make_video_script", "srt")
ENV_FILE = os.path.join(BASE_DIR, "92_make_image", ".env")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
VIDEO_FPS = 24

# 한글 폰트 (Windows)
FONT_PATH = "C:/Windows/Fonts/malgunbd.ttf"  # 맑은 고딕 Bold
if not os.path.exists(FONT_PATH):
    FONT_PATH = "C:/Windows/Fonts/malgun.ttf"  # 맑은 고딕 Regular


def safe_print(text):
    """cp949 인코딩 안전 출력"""
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def load_api_key():
    """환경변수 또는 .env 파일에서 Gemini API 키 로드"""
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
    """Gemini로 가사 번역 (ko→en 또는 en→ko), 줄 단위 대응"""
    api_key = load_api_key()
    if not api_key:
        safe_print("  Gemini API 키 없음 — 번역 건너뜀")
        return None

    if source_lang == 'ko':
        target = "English"
        instruction = "Translate each Korean lyrics line to natural English."
    else:
        target = "Korean (한국어)"
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
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        result_text = response.text.strip()

        # 번호 제거하여 줄 리스트로 파싱
        translated = []
        for line in result_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            # "1. text" or "1) text" 형식 제거
            cleaned = re.sub(r'^\d+[\.\)]\s*', '', line)
            translated.append(cleaned)

        if len(translated) != len(lines):
            safe_print(f"  번역 줄 수 불일치: 원본 {len(lines)}줄 vs 번역 {len(translated)}줄")
            # 부족하면 빈 문자열로 채우고, 넘치면 자름
            while len(translated) < len(lines):
                translated.append("")
            translated = translated[:len(lines)]

        return translated

    except Exception as e:
        safe_print(f"  번역 오류: {e}")
        return None


# Whisper 환각(hallucination) 블랙리스트
WHISPER_HALLUCINATIONS = [
    '이 영상은 유료 광고를 포함하고 있습니다',
    '자막 제공',
    '시청해 주셔서 감사합니다',
    '구독과 좋아요',
    '다음 영상에서 만나요',
    'Thank you for watching',
    'Subscribe and like',
    'Please subscribe',
    'See you in the next video',
    'MBC 뉴스',
    'KBS 뉴스',
    'SBS 뉴스',
]


def is_hallucination(text):
    """Whisper 환각 문구인지 판별"""
    for h in WHISPER_HALLUCINATIONS:
        if h in text:
            return True
    return False


def extract_lyrics_from_prompt(base_name):
    """04_Suno_Prompt에서 실제 가사 줄 목록 추출 (구조태그 제외)"""
    prompt_path = os.path.join(PROMPT_DIR, f"{base_name}.md")
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
        if not line:
            continue
        # 구조태그 제거: [Intro], [Verse 1], [Chorus] 등
        if re.match(r'^\[.*\]$', line):
            continue
        lines.append(line)

    return lines


def similarity(a, b):
    """두 문자열의 유사도 (0~1)"""
    return SequenceMatcher(None, a, b).ratio()


def match_lyrics_to_segments(filtered_segments, actual_lyrics):
    """Whisper 세그먼트의 텍스트를 실제 가사와 매칭하여 보정

    전략: Whisper 세그먼트를 순서대로 처리하면서,
    실제 가사도 순서대로 소비한다 (가사는 곡 순서대로 나오므로).
    각 세그먼트에 대해 현재 위치 주변에서 가장 유사한 가사 줄을 찾는다.
    """
    if not actual_lyrics:
        return filtered_segments

    result = []
    lyrics_pos = 0  # 현재 가사 탐색 위치
    window = 5  # 전후 탐색 범위

    for seg in filtered_segments:
        whisper_text = seg['text']

        # 탐색 범위: 현재 위치 기준 전후 window
        search_start = max(0, lyrics_pos - 2)
        search_end = min(len(actual_lyrics), lyrics_pos + window)

        best_score = 0
        best_idx = -1
        best_text = whisper_text  # 기본값은 Whisper 원문

        for i in range(search_start, search_end):
            score = similarity(whisper_text, actual_lyrics[i])
            if score > best_score:
                best_score = score
                best_idx = i
                best_text = actual_lyrics[i]

        # 유사도 0.3 이상이면 실제 가사로 치환
        if best_score >= 0.3:
            result.append({
                'start': seg['start'],
                'end': seg['end'],
                'text': best_text,
                'whisper_text': whisper_text,
                'match_score': best_score,
            })
            # 매칭된 위치 다음으로 이동
            lyrics_pos = best_idx + 1
        else:
            # 매칭 실패 — Whisper 원문 유지
            result.append({
                'start': seg['start'],
                'end': seg['end'],
                'text': whisper_text,
                'whisper_text': whisper_text,
                'match_score': 0,
            })

    return result


def assign_lyrics_by_timing(filtered_segments, actual_lyrics):
    """Whisper 세그먼트의 타이밍만 사용하고, 실제 가사를 순서대로 배정

    Whisper의 텍스트 인식은 무시하고 음성 구간 타이밍만 활용.
    실제 가사를 순서대로 타이밍 슬롯에 매핑.

    세그먼트 수와 가사 수가 다를 경우:
    - 세그먼트 > 가사: 인접 세그먼트를 병합하여 가사 수에 맞춤
    - 세그먼트 < 가사: 긴 세그먼트를 분할하여 가사 수에 맞춤
    """
    if not actual_lyrics:
        return filtered_segments

    num_seg = len(filtered_segments)
    num_lyr = len(actual_lyrics)

    safe_print(f"    VAD 매핑: 세그먼트 {num_seg}개 → 가사 {num_lyr}줄")

    if num_seg == num_lyr:
        # 완벽한 1:1 매핑
        result = []
        for seg, lyric in zip(filtered_segments, actual_lyrics):
            result.append({
                'start': seg['start'],
                'end': seg['end'],
                'text': lyric,
                'whisper_text': seg['text'],
            })
        return result

    if num_seg > num_lyr:
        # 세그먼트가 더 많음 → 인접 세그먼트 병합
        # 가장 가까운 세그먼트 쌍을 반복 병합
        segs = [dict(s) for s in filtered_segments]
        while len(segs) > num_lyr:
            # 가장 짧은 간격의 인접 쌍 찾기
            min_gap = float('inf')
            merge_idx = 0
            for i in range(len(segs) - 1):
                gap = segs[i + 1]['start'] - segs[i]['end']
                if gap < min_gap:
                    min_gap = gap
                    merge_idx = i
            # 병합
            segs[merge_idx]['end'] = segs[merge_idx + 1]['end']
            segs[merge_idx]['text'] = segs[merge_idx]['text'] + ' ' + segs[merge_idx + 1]['text']
            segs.pop(merge_idx + 1)

        result = []
        for seg, lyric in zip(segs, actual_lyrics):
            result.append({
                'start': seg['start'],
                'end': seg['end'],
                'text': lyric,
                'whisper_text': seg.get('text', ''),
            })
        return result

    # num_seg < num_lyr → 세그먼트가 부족, 긴 세그먼트를 분할
    segs = [dict(s) for s in filtered_segments]
    while len(segs) < num_lyr:
        # 가장 긴 세그먼트 찾아서 분할
        max_dur = 0
        split_idx = 0
        for i, seg in enumerate(segs):
            dur = seg['end'] - seg['start']
            if dur > max_dur:
                max_dur = dur
                split_idx = i

        seg = segs[split_idx]
        mid = (seg['start'] + seg['end']) / 2
        seg1 = {'start': seg['start'], 'end': mid, 'text': seg.get('text', '')}
        seg2 = {'start': mid, 'end': seg['end'], 'text': ''}
        segs[split_idx:split_idx + 1] = [seg1, seg2]

    result = []
    for seg, lyric in zip(segs, actual_lyrics):
        result.append({
            'start': seg['start'],
            'end': seg['end'],
            'text': lyric,
            'whisper_text': seg.get('text', ''),
        })
    return result


def detect_song_info(prompt_filename):
    """04_Suno_Prompt에서 곡 정보 파싱 (인스트루멘탈 여부, 언어)"""
    prompt_path = os.path.join(PROMPT_DIR, prompt_filename)
    if not os.path.exists(prompt_path):
        return None

    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Style of Music 섹션 추출
    style_match = re.search(r'## Style of Music\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
    style_text = style_match.group(1).strip() if style_match else ""

    # 인스트루멘탈 판별
    is_instrumental = any(kw in style_text.lower() for kw in [
        'instrumental only', 'no vocals', 'no singing', 'no voice'
    ])
    # "no vocals" 가 있어도 "Female Vocal" 등이 Style에 포함되면 보컬곡
    has_vocal_tag = any(kw in style_text for kw in [
        'Female Vocal', 'Male Vocal', 'Vocal', 'vocals'
    ])
    if has_vocal_tag and 'no vocal' not in style_text.lower():
        is_instrumental = False

    # 언어 판별
    if 'Korean lyrics' in style_text or 'Korean' in style_text:
        language = 'ko'
    elif 'English lyrics' in style_text or 'English' in style_text:
        language = 'en'
    else:
        # Lyrics 섹션에서 한글 존재 여부로 판별
        lyrics_match = re.search(r'## Lyrics\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
        lyrics_text = lyrics_match.group(1) if lyrics_match else ""
        has_korean = bool(re.search(r'[가-힣]', lyrics_text))
        language = 'ko' if has_korean else 'en'

    return {
        'is_instrumental': is_instrumental,
        'language': language,
        'style': style_text,
    }


def find_vocal_songs():
    """보컬이 있는 곡만 필터링하여 목록 반환"""
    mp3_files = sorted(glob.glob(os.path.join(MP3_DIR, "*_v*.mp3")))
    songs = []

    for mp3_path in mp3_files:
        mp3_basename = os.path.splitext(os.path.basename(mp3_path))[0]
        version_match = re.match(r'^(.+)_(v\d+)$', mp3_basename)
        if not version_match:
            continue

        base_name = version_match.group(1)
        version = version_match.group(2)

        # 이미지 확인
        img_path = os.path.join(IMG_DIR, f"{base_name}_{version}.png")
        if not os.path.exists(img_path):
            continue

        # 프롬프트 파일 찾기
        prompt_file = f"{base_name}.md"
        info = detect_song_info(prompt_file)
        if info is None:
            continue

        # 인스트루멘탈은 건너뜀
        if info['is_instrumental']:
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
    """초를 SRT 시간 형식으로 변환: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_audio_with_moviepy_ffmpeg(mp3_path):
    """moviepy의 ffmpeg 바이너리를 사용하여 오디오를 numpy 배열로 로드"""
    import numpy as np
    cmd = [
        FFMPEG_BINARY,
        '-nostdin',
        '-threads', '0',
        '-i', mp3_path,
        '-f', 's16le',
        '-ac', '1',
        '-acodec', 'pcm_s16le',
        '-ar', '16000',
        '-',
    ]
    result = subprocess.run(cmd, capture_output=True)
    audio = np.frombuffer(result.stdout, np.int16).flatten().astype(np.float32) / 32768.0
    return audio


def transcribe_to_srt(mp3_path, srt_path, language='ko', base_name=None):
    """Whisper로 mp3를 분석하여 SRT 파일 생성
    - 환각 필터링
    - 실제 가사 MD와 대조하여 텍스트 보정
    """
    if os.path.exists(srt_path):
        safe_print(f"  SRT 이미 존재: {os.path.basename(srt_path)} (재사용)")
        return True

    # 한국어/영어 모두 medium 모델 사용
    model_name = "large-v3"
    safe_print(f"  Whisper 모델 로드: {model_name} (언어: {language})")

    model = whisper.load_model(model_name)

    safe_print(f"  오디오 로드 중...")
    audio_data = load_audio_with_moviepy_ffmpeg(mp3_path)

    # 실제 가사를 initial_prompt로 제공하여 인식률 향상
    initial_prompt = None
    if base_name:
        actual_lyrics = extract_lyrics_from_prompt(base_name)
        if actual_lyrics:
            initial_prompt = ", ".join(actual_lyrics)
            safe_print(f"  initial_prompt 제공: {len(actual_lyrics)}줄 가사")

    safe_print(f"  음성 인식 중... (시간이 걸릴 수 있습니다)")
    result = model.transcribe(
        audio_data,
        language=language,
        word_timestamps=True,
        verbose=False,
        initial_prompt=initial_prompt,
    )

    # 세그먼트 단위로 필터링
    segments = result.get('segments', [])
    if not segments:
        safe_print(f"  인식된 가사 없음!")
        return False

    filtered = []
    hallucination_count = 0
    for seg in segments:
        start = seg['start']
        end = seg['end']
        text = seg['text'].strip()

        if not text:
            continue
        if re.match(r'^\[.*\]$', text):
            continue
        if end - start < 0.5:
            continue
        # 환각 필터
        if is_hallucination(text):
            hallucination_count += 1
            continue
        if language == 'ko' and not re.search(r'[가-힣]', text):
            continue
        if language == 'en' and not re.search(r'[a-zA-Z]', text):
            continue
        if len(text) <= 2 and re.match(r'^[으아오음허흐]+$', text):
            continue

        filtered.append({'start': start, 'end': end, 'text': text})

    if hallucination_count > 0:
        safe_print(f"  환각 필터링: {hallucination_count}개 제거")

    if not filtered:
        safe_print(f"  필터링 후 유효 가사 없음 (환각만 감지됨)")
        return False

    # 실제 가사로 텍스트 교체 (VAD 순서 매핑)
    if base_name:
        actual_lyrics = extract_lyrics_from_prompt(base_name)
        if actual_lyrics:
            safe_print(f"  VAD 순서 매핑: Whisper 타이밍 {len(filtered)}개 + 실제 가사 {len(actual_lyrics)}줄")
            filtered = assign_lyrics_by_timing(filtered, actual_lyrics)

    # Gemini로 번역 (ko→en, en→ko)
    original_lines = [seg['text'] for seg in filtered]
    safe_print(f"  가사 번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)

    # SRT 파일 생성 (원문 + 번역)
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(filtered):
            f.write(f"{i + 1}\n")
            f.write(f"{format_srt_time(seg['start'])} --> {format_srt_time(seg['end'])}\n")
            f.write(f"{seg['text']}\n")
            if translations and translations[i]:
                f.write(f"{translations[i]}\n")
            f.write(f"\n")

    safe_print(f"  SRT 생성 완료: {len(filtered)}개 세그먼트 (이중언어)")
    return True


def get_audio_duration(mp3_path):
    """ffmpeg로 실제 오디오 길이 측정"""
    result = subprocess.run(
        [FFMPEG_BINARY, '-i', mp3_path, '-f', 'null', '-'],
        capture_output=True, text=True
    )
    total_duration = None
    for line in result.stderr.split('\n'):
        if 'time=' in line:
            m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if m:
                total_duration = (int(m.group(1)) * 3600 +
                                  int(m.group(2)) * 60 +
                                  float(m.group(3)))
    return total_duration


def generate_circular_equalizer(mp3_path, eq_video_path, duration, size=480, fps=24):
    """MP3에서 원형 막대 이퀄라이저 영상 생성 (투명 배경, 음량 반응 원 크기)"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    import math

    if os.path.exists(eq_video_path):
        safe_print(f"  이퀄라이저 이미 존재: {eq_video_path}")
        return eq_video_path

    safe_print("  원형 이퀄라이저 생성 중...")

    # FFmpeg로 오디오를 raw PCM으로 디코딩
    decode_cmd = [
        FFMPEG_BINARY, '-i', mp3_path,
        '-f', 's16le', '-ac', '1', '-ar', '44100',
        '-t', str(duration), '-v', 'quiet', '-'
    ]
    result = subprocess.run(decode_cmd, capture_output=True)
    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)

    sample_rate = 44100
    num_frames = int(duration * fps)
    samples_per_frame = max(1, len(samples) // num_frames)

    # 안티앨리어싱: 2배 크기로 그린 뒤 축소
    render_size = size * 2
    num_bars = 48  # FFT 분석용 밴드 수 (보간 후 96개로 렌더링)
    cx = render_size // 2
    cy = render_size // 2
    base_inner_radius = int(render_size * 0.18)
    max_bar_height = int(render_size * 0.23)
    bar_width = 4  # 2배 스케일 (막대 96개로 증가하여 폭 축소)
    # 음량에 의한 원 크기 변화 범위 (기본 반지름의 ±30%)
    radius_scale_range = 0.30

    # FFmpeg process to encode equalizer video (QuickTime Animation, alpha 지원)
    encode_cmd = [
        FFMPEG_BINARY, '-y',
        '-f', 'rawvideo', '-pix_fmt', 'rgba',
        '-s', f'{size}x{size}', '-r', str(fps),
        '-i', 'pipe:0',
        '-c:v', 'qtrle',
        '-v', 'quiet',
        eq_video_path
    ]
    proc = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE)

    prev_mags = np.zeros(num_bars)
    prev_volume = 0.0
    volume_data = []  # 프레임별 음량 저장

    for i in range(num_frames):
        start = i * samples_per_frame
        end = min(start + samples_per_frame, len(samples))
        chunk = samples[start:end]

        if len(chunk) < 512:
            chunk = np.pad(chunk, (0, 512 - len(chunk)))

        # 평균 음량 (RMS)
        rms = np.sqrt(np.mean(chunk ** 2)) / 32768.0
        rms = min(rms * 3.0, 1.0)  # 스케일링 후 클램프
        # 음량 스무딩 (댐핑 강화)
        volume = prev_volume * 0.7 + rms * 0.3
        prev_volume = volume

        # 음량에 따라 inner_radius 동적 조절
        inner_radius = int(base_inner_radius * (1.0 + radius_scale_range * volume))
        volume_data.append(volume)

        # Window + FFT
        window = np.hanning(len(chunk))
        fft_data = np.abs(np.fft.rfft(chunk * window))

        # Log-scale frequency bands
        freqs = np.fft.rfftfreq(len(chunk), 1.0 / sample_rate)
        log_bands = np.logspace(np.log10(30), np.log10(15000), num_bars + 1)
        magnitudes = np.zeros(num_bars)
        for j in range(num_bars):
            mask = (freqs >= log_bands[j]) & (freqs < log_bands[j + 1])
            if mask.any():
                magnitudes[j] = np.mean(fft_data[mask])

        # Normalize
        max_mag = magnitudes.max()
        if max_mag > 0:
            magnitudes = magnitudes / max_mag

        # Temporal smoothing (잔상 감소)
        magnitudes = prev_mags * 0.3 + magnitudes * 0.7
        prev_mags = magnitudes.copy()

        # 보간: 48개 → 96개 (사이 막대는 인접 두 값의 평균)
        interp_mags = np.zeros(num_bars * 2)
        for j in range(num_bars):
            interp_mags[j * 2] = magnitudes[j]
            interp_mags[j * 2 + 1] = (magnitudes[j] + magnitudes[(j + 1) % num_bars]) / 2.0
        render_num_bars = num_bars * 2

        # Draw bars on separate layer (2x 렌더링)
        bar_layer = Image.new('RGBA', (render_size, render_size), (0, 0, 0, 0))
        bar_draw = ImageDraw.Draw(bar_layer)

        for j, mag in enumerate(interp_mags):
            angle = (j / render_num_bars) * 2 * math.pi - math.pi / 2
            bar_len = max(2, mag * max_bar_height)
            outer_len = bar_len * 0.8
            inner_len = bar_len * 0.2

            x1 = cx + math.cos(angle) * (inner_radius - inner_len)
            y1 = cy + math.sin(angle) * (inner_radius - inner_len)
            x2 = cx + math.cos(angle) * (inner_radius + outer_len)
            y2 = cy + math.sin(angle) * (inner_radius + outer_len)

            bar_draw.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 255), width=bar_width)

        # Inner circle ring
        ring_r = inner_radius - 2
        bar_draw.ellipse(
            [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
            outline=(255, 255, 255, 100), width=1
        )

        # Layer 1: 블랙 50% 배경 분리 레이어 (가장 넓은 글로우)
        black_glow = bar_layer.copy()
        # 알파 채널만 추출하여 블랙으로 채움
        r, g, b, a = black_glow.split()
        black_glow = Image.merge('RGBA', (
            Image.new('L', (render_size, render_size), 0),
            Image.new('L', (render_size, render_size), 0),
            Image.new('L', (render_size, render_size), 0),
            a,
        ))
        black_glow = black_glow.filter(ImageFilter.GaussianBlur(radius=24))  # 2x scale
        # 알파를 50%로 조절
        r, g, b, a = black_glow.split()
        a = a.point(lambda x: x // 2)
        black_glow = Image.merge('RGBA', (r, g, b, a))

        # Layer 2: 화이트 글로우 레이어 (중간 블러)
        white_glow = bar_layer.filter(ImageFilter.GaussianBlur(radius=12))  # 2x scale

        # 합성: 블랙 배경 → 화이트 글로우 → 원본 막대
        img = Image.new('RGBA', (render_size, render_size), (0, 0, 0, 0))
        img = Image.alpha_composite(img, black_glow)
        img = Image.alpha_composite(img, white_glow)
        img = Image.alpha_composite(img, bar_layer)

        # 안티앨리어싱: 2x → 1x 축소
        img = img.resize((size, size), Image.LANCZOS)

        proc.stdin.write(img.tobytes())

        if (i + 1) % (fps * 30) == 0:
            safe_print(f"    이퀄라이저 렌더링: {i + 1}/{num_frames} 프레임")

    proc.stdin.close()
    proc.wait()

    # 음량 데이터로 FFmpeg sendcmd 파일 생성 (배경 밝기+감마 펄스)
    cmd_path = eq_video_path.replace('.mov', '_cmd.txt')
    with open(cmd_path, 'w') as f:
        for idx, vol in enumerate(volume_data):
            t = idx / fps
            # 음량 높을수록 밝게 (최대 +0.15)
            brightness = 0.15 * vol
            # 음량 높을수록 밝은 부분이 더 밝게 (gamma: 1.0 → 0.6, 낮을수록 밝아짐)
            gamma = 1.0 - 0.4 * vol
            f.write(f"{t:.4f} [enter] eq@bgpulse brightness {brightness:.4f};\n")
            f.write(f"{t:.4f} [enter] eq@bgpulse gamma {gamma:.4f};\n")
    safe_print(f"  배경 펄스 커맨드 생성: {cmd_path}")

    safe_print(f"  이퀄라이저 생성 완료: {eq_video_path}")
    return eq_video_path


def create_video_with_srt(song, srt_path):
    """이미지 + mp3 + SRT 자막 → mp4 생성 (ffmpeg 자막 필터 사용)"""
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

    # 원형 이퀄라이저 영상 생성
    eq_video_path = os.path.join(SRT_DIR, f"{song['name']}_eq.mov")
    generate_circular_equalizer(mp3_path, eq_video_path, duration)

    # SRT 파일 경로를 ffmpeg용으로 변환 (Windows 역슬래시 → 슬래시, 콜론 이스케이프)
    srt_escaped = srt_path.replace('\\', '/').replace(':', '\\:')

    # sendcmd 파일 경로
    cmd_file = eq_video_path.replace('.mov', '_cmd.txt').replace('\\', '/').replace(':', '\\:')

    # ffmpeg로 이미지 + 오디오 + 이퀄라이저 + 자막 합성 (배경 밝기 펄스 포함)
    safe_print("  mp4 생성 중 (자막 + 이퀄라이저 + 배경펄스 포함)...")
    filter_complex = (
        f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"sendcmd=f='{cmd_file}',"
        f"eq@bgpulse=brightness=0:gamma=1[bg];"
        f"[bg][2:v]overlay=(W-w)/2:(H-h)/2,"
        f"subtitles='{srt_escaped}':force_style='"
        f"FontName=Malgun Gothic,"
        f"FontSize=20,"
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
        '-loop', '1',
        '-i', img_path,
        '-i', mp3_path,
        '-i', eq_video_path,
        '-filter_complex', filter_complex,
        '-map', '[out]',
        '-map', '1:a',
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '23',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-t', str(duration),
        '-shortest',
        '-y',
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        safe_print(f"  ffmpeg 오류: {result.stderr[-500:]}")
        return None

    file_size = os.path.getsize(output_path)
    safe_print(f"  저장 완료: {output_path} ({file_size // (1024*1024)} MB)")
    return output_path


def create_video_no_subtitle(song):
    """자막 없이 이미지 + mp3 → mp4 생성 (가사 인식 실패 시 폴백)"""
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

    # 원형 이퀄라이저 영상 생성
    eq_video_path = os.path.join(SRT_DIR, f"{song['name']}_eq.mov")
    generate_circular_equalizer(mp3_path, eq_video_path, duration)

    # sendcmd 파일 경로
    cmd_file = eq_video_path.replace('.mov', '_cmd.txt').replace('\\', '/').replace(':', '\\:')

    safe_print(f"  자막 없이 mp4 생성 중 (이퀄라이저 + 배경펄스 포함)... ({duration:.1f}초)")
    filter_complex = (
        f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"sendcmd=f='{cmd_file}',"
        f"eq@bgpulse=brightness=0:gamma=1[bg];"
        f"[bg][2:v]overlay=(W-w)/2:(H-h)/2[out]"
    )
    cmd = [
        FFMPEG_BINARY,
        '-loop', '1',
        '-i', img_path,
        '-i', mp3_path,
        '-i', eq_video_path,
        '-filter_complex', filter_complex,
        '-map', '[out]',
        '-map', '1:a',
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '23',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-t', str(duration),
        '-shortest',
        '-y',
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        safe_print(f"  ffmpeg 오류: {result.stderr[-500:]}")
        return None

    file_size = os.path.getsize(output_path)
    safe_print(f"  저장 완료 (자막없음): {output_path} ({file_size // (1024*1024)} MB)")
    return output_path


def main():
    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(SRT_DIR, exist_ok=True)

    songs = find_vocal_songs()
    if not songs:
        safe_print("가사 영상을 생성할 보컬 곡이 없습니다.")
        return

    safe_print(f"\n보컬 곡 목록 ({len(songs)}개):")
    for i, song in enumerate(songs):
        safe_print(f"  [{i}] {song['name']} ({song['language']})")

    for song in songs:
        safe_print(f"\n처리: {song['name']}")

        # 이미 영상이 있으면 건너뜀
        output_path = os.path.join(VIDEO_DIR, f"{song['name']}.mp4")
        if os.path.exists(output_path):
            safe_print(f"  이미 존재: {output_path} (건너뜁니다)")
            continue

        try:
            # 1. Whisper로 가사 타이밍 추출 → SRT (실제 가사 대조 포함)
            srt_path = os.path.join(SRT_DIR, f"{song['name']}.srt")
            success = transcribe_to_srt(
                song['mp3'], srt_path, song['language'],
                base_name=song['base_name'],
            )
            if not success:
                safe_print(f"  가사 인식 실패 → 자막 없이 영상 생성")
                create_video_no_subtitle(song)
                continue

            # 2. 이미지 + mp3 + SRT → mp4
            create_video_with_srt(song, srt_path)

        except Exception as e:
            safe_print(f"  오류: {e}")

    safe_print("\n모든 가사 영상 생성 완료!")


if __name__ == "__main__":
    main()
