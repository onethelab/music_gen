"""
테스트: demucs 보컬 분리 + Whisper transcribe + 실제 가사 매핑
- 보컬 트랙에 Whisper를 적용하여 타이밍 추출
- assign_lyrics_by_timing으로 실제 가사 텍스트 교체
"""

import os
import sys
import re
import subprocess

# 프로젝트 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "95_make_video_script"))

from moviepy.config import FFMPEG_BINARY

_ffmpeg_dir = os.path.dirname(FFMPEG_BINARY)
if _ffmpeg_dir not in os.environ.get('PATH', ''):
    os.environ['PATH'] = _ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

import whisper
from align_lyrics import (
    separate_vocals, extract_lyrics_from_prompt, detect_language,
    format_srt_time, translate_lyrics, safe_print,
    MP3_DIR, PROMPT_DIR, SRT_DIR, VOCAL_DIR
)
from video_with_lyrics import assign_lyrics_by_timing, is_hallucination


def whisper_on_vocals(mp3_path, srt_path, base_name, language='en'):
    """demucs 보컬 분리 → Whisper transcribe → 실제 가사 매핑"""
    # 가사 로드
    actual_lyrics = extract_lyrics_from_prompt(base_name)
    if not actual_lyrics:
        safe_print(f"  가사를 찾을 수 없음: {base_name}")
        return False

    safe_print(f"  가사 {len(actual_lyrics)}줄 로드 완료")

    # demucs 보컬 분리 (캐시 재사용)
    target_name = os.path.splitext(os.path.basename(mp3_path))[0]
    vocal_path = separate_vocals(mp3_path, target_name)
    if not vocal_path:
        safe_print(f"  보컬 분리 실패")
        return False

    # Whisper로 보컬 트랙 transcribe
    safe_print(f"  Whisper 모델 로드: large-v3")
    model = whisper.load_model("large-v3")

    safe_print(f"  보컬 오디오 로드 중...")
    decode_cmd = [
        FFMPEG_BINARY, '-i', vocal_path,
        '-f', 'f32le', '-ac', '1', '-ar', '16000',
        '-v', 'quiet', '-'
    ]
    proc = subprocess.run(decode_cmd, capture_output=True)
    import numpy as np
    audio_np = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    safe_print(f"  오디오 로드: {len(audio_np)/16000:.1f}초")

    # initial_prompt로 가사 힌트 제공
    initial_prompt = ", ".join(actual_lyrics)

    safe_print(f"  Whisper transcribe 중...")
    result = model.transcribe(
        audio_np,
        language=language,
        word_timestamps=True,
        verbose=False,
        initial_prompt=initial_prompt,
    )

    # 세그먼트 필터링
    segments = result.get('segments', [])
    safe_print(f"  Whisper 원본 세그먼트: {len(segments)}개")

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
        if is_hallucination(text):
            hallucination_count += 1
            continue
        if language == 'en' and not re.search(r'[a-zA-Z]', text):
            continue
        if language == 'ko' and not re.search(r'[가-힣]', text):
            continue

        filtered.append({'start': start, 'end': end, 'text': text})

    if hallucination_count > 0:
        safe_print(f"  환각 필터링: {hallucination_count}개 제거")

    safe_print(f"  필터링 후 세그먼트: {len(filtered)}개")

    # Whisper가 인식한 내용 출력
    safe_print(f"\n  === Whisper 인식 결과 (보컬 트랙) ===")
    for i, seg in enumerate(filtered):
        safe_print(f"    [{i+1:2d}] {seg['start']:6.2f}~{seg['end']:6.2f}  {seg['text']}")

    # 실제 가사로 텍스트 교체
    safe_print(f"\n  === 실제 가사 매핑 ===")
    mapped = assign_lyrics_by_timing(filtered, actual_lyrics)

    safe_print(f"\n  === 최종 결과 ===")
    for i, seg in enumerate(mapped):
        safe_print(f"    [{i+1:2d}] {seg['start']:6.2f}~{seg['end']:6.2f}  {seg['text']}")

    # 번역
    original_lines = [seg['text'] for seg in mapped]
    safe_print(f"\n  가사 번역 중... ({len(original_lines)}줄)")
    translations = translate_lyrics(original_lines, language)

    # SRT 저장
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(mapped):
            f.write(f"{i + 1}\n")
            f.write(f"{format_srt_time(seg['start'])} --> {format_srt_time(seg['end'])}\n")
            f.write(f"{seg['text']}\n")
            if translations and i < len(translations) and translations[i]:
                f.write(f"{translations[i]}\n")
            f.write(f"\n")

    safe_print(f"\n  SRT 저장: {srt_path}")
    return True


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "79_Dead_Code_v1"

    version_match = re.match(r'^(.+)_(v\d+)$', target)
    base_name = version_match.group(1) if version_match else target

    mp3_path = os.path.join(MP3_DIR, f"{target}.mp3")
    srt_path = os.path.join(SRT_DIR, f"{target}_whisper_test.srt")

    if not os.path.exists(mp3_path):
        safe_print(f"mp3 파일 없음: {mp3_path}")
        sys.exit(1)

    language = detect_language(base_name)
    safe_print(f"테스트: {target} (언어: {language})")
    whisper_on_vocals(mp3_path, srt_path, base_name, language)
