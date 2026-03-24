"""WhisperX Raw 자막으로 MP4 생성 — 타이밍 검증용"""
import os
import sys
import subprocess
import torch
import whisperx
import numpy as np

from moviepy.config import FFMPEG_BINARY

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
IMG_DIR = os.path.join(BASE_DIR, "06_img")
VIDEO_DIR = os.path.join(BASE_DIR, "07_Video")
SRT_DIR = os.path.join(BASE_DIR, "95_make_video_script", "srt")
VOCAL_DIR = os.path.join(BASE_DIR, "95_make_video_script", "vocals")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720


def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "04_Last_Train_Cassette_v1"
    language = sys.argv[2] if len(sys.argv) > 2 else "ja"

    base_name = target.rsplit('_v', 1)[0]
    version = 'v' + target.rsplit('_v', 1)[1]

    mp3_path = os.path.join(MP3_DIR, f"{target}.mp3")
    img_path = os.path.join(IMG_DIR, f"{base_name}_{version}.png")
    vocal_path = os.path.join(VOCAL_DIR, f"{target}_vocals.wav")
    srt_path = os.path.join(SRT_DIR, f"{target}_raw.srt")
    output_path = os.path.join(VIDEO_DIR, f"{target}_raw.mp4")

    # WhisperX transcribe + align
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    print(f"WhisperX transcribe ({device})...")
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=language,
                                 vad_options={"vad_onset": 0.3, "vad_offset": 0.21})
    audio = whisperx.load_audio(vocal_path)
    chunk_size = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    print(f"chunk_size: {chunk_size}")
    result = model.transcribe(audio, batch_size=16, language=language, chunk_size=chunk_size)

    print(f"WhisperX align...")
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result['segments'], align_model, metadata, audio, device, return_char_alignments=False)

    segments = result['segments']
    print(f"세그먼트: {len(segments)}개")

    # Raw SRT 생성 (세그먼트 그대로)
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(segments):
            start = seg.get('start', 0)
            end = seg.get('end', 0)
            text = seg.get('text', '').strip()
            if not text:
                continue
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(start)} --> {format_srt_time(end)}\n")
            f.write(f"{text}\n\n")
            try:
                print(f"  [{i+1}] {start:.1f}~{end:.1f} {text[:60]}")
            except UnicodeEncodeError:
                print(f"  [{i+1}] {start:.1f}~{end:.1f} (encoding error)")

    print(f"\nSRT 저장: {srt_path}")

    # MP4 생성
    import re
    result2 = subprocess.run([FFMPEG_BINARY, '-i', mp3_path, '-f', 'null', '-'], capture_output=True, text=True)
    duration = None
    for line in result2.stderr.split('\n'):
        if 'time=' in line:
            m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if m:
                duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    srt_escaped = srt_path.replace('\\', '/').replace(':', '\\:')

    print("MP4 생성 중...")
    filter_complex = (
        f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
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
        '-filter_complex', filter_complex,
        '-map', '[out]', '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
        '-c:a', 'aac', '-b:a', '192k',
        '-t', str(duration), '-shortest', '-y',
        output_path,
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ffmpeg 오류: {r.stderr[-500:]}")
        return

    size = os.path.getsize(output_path)
    print(f"완료: {output_path} ({size // (1024*1024)} MB)")


if __name__ == "__main__":
    main()
