"""
mp3 + png → mp4 변환 자동화 (v1/v2 개별 생성)
- 05_Mp3/의 v1, v2 mp3 각각에 대해
- 06_img/의 대응하는 v1, v2 이미지를 배경으로 mp4 생성
- 07_Video/에 저장

사용법:
    cd 93_make_video
    python video_create.py
"""

import os
import glob
import re
import subprocess

from moviepy import ImageClip, AudioFileClip
from moviepy.config import FFMPEG_BINARY

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
IMG_DIR = os.path.join(BASE_DIR, "06_img")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
VIDEO_DIR = os.path.join(BASE_DIR, "07_Video")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
VIDEO_FPS = 24


def safe_print(text):
    """cp949 인코딩 안전 출력"""
    print(text.encode('cp949', errors='ignore').decode('cp949'))


def find_songs():
    """05_Mp3/에서 mp3 파일을 기준으로 v1/v2 개별 곡 목록 생성"""
    mp3_files = sorted(glob.glob(os.path.join(MP3_DIR, "*_v*.mp3")))
    songs = []

    for mp3_path in mp3_files:
        mp3_basename = os.path.splitext(os.path.basename(mp3_path))[0]
        # "05_Gangnam_Lovers_Fight_v1" → base="05_Gangnam_Lovers_Fight", version="v1"
        version_match = re.match(r'^(.+)_(v\d+)$', mp3_basename)
        if not version_match:
            continue

        base_name = version_match.group(1)
        version = version_match.group(2)

        # 해당 버전의 이미지 찾기 (예: 05_Gangnam_Lovers_Fight_v1.png)
        img_path = os.path.join(IMG_DIR, f"{base_name}_{version}.png")
        if not os.path.exists(img_path):
            safe_print(f"  이미지 없음: {base_name}_{version}.png (건너뜁니다)")
            continue

        songs.append({
            'name': f"{base_name}_{version}",
            'base_name': base_name,
            'version': version,
            'img': img_path,
            'mp3': mp3_path,
        })

    return songs


def create_video(song):
    """이미지 + mp3 → mp4 생성 (v1/v2 개별)"""
    img_path = song['img']
    mp3_path = song['mp3']
    output_path = os.path.join(VIDEO_DIR, f"{song['name']}.mp4")

    if os.path.exists(output_path):
        safe_print(f"  이미 존재: {output_path} (건너뜁니다)")
        return output_path

    safe_print(f"  이미지: {os.path.basename(img_path)}")
    safe_print(f"  오디오: {os.path.basename(mp3_path)}")

    # ffmpeg로 실제 디코딩된 duration 측정 (Suno mp3는 VBR이라 헤더 추정값이 부정확)
    result = subprocess.run(
        [FFMPEG_BINARY, '-i', mp3_path, '-f', 'null', '-'],
        capture_output=True, text=True
    )
    total_duration = None
    for line in result.stderr.split('\n'):
        if 'time=' in line:
            # "time=00:02:25.27" 형식에서 초 추출
            import re as _re
            m = _re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if m:
                total_duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    if total_duration is None:
        safe_print(f"  ffmpeg duration 측정 실패, moviepy 값 사용")
        audio = AudioFileClip(mp3_path)
        total_duration = audio.duration
    else:
        safe_print(f"  오디오 길이: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
        audio = AudioFileClip(mp3_path)
        if abs(audio.duration - total_duration) > 1:
            # 안전한 duration 선택: 둘 중 짧은 값 사용
            safe_duration = min(audio.duration, total_duration)
            safe_print(f"  duration 보정: {audio.duration:.1f}초 → {safe_duration:.1f}초")
            total_duration = safe_duration
            audio = audio.subclipped(0, total_duration)

    # 이미지를 오디오 길이만큼 표시
    video = ImageClip(img_path).with_duration(total_duration).resized((TARGET_WIDTH, TARGET_HEIGHT))
    video = video.with_audio(audio)

    safe_print("  mp4 생성 중...")
    video.write_videofile(
        output_path,
        fps=VIDEO_FPS,
        codec="libx264",
        audio_codec="aac",
        audio_bitrate="192k",
        logger=None,
    )

    # 리소스 정리
    audio.close()
    video.close()

    file_size = os.path.getsize(output_path)
    safe_print(f"  저장 완료: {output_path} ({file_size // (1024*1024)} MB)")
    return output_path


def main():
    os.makedirs(VIDEO_DIR, exist_ok=True)

    songs = find_songs()
    if not songs:
        safe_print("생성할 곡이 없습니다. 06_img/에 이미지가 있는지 확인하세요.")
        return

    safe_print(f"\n곡 목록:")
    for i, song in enumerate(songs):
        safe_print(f"  [{i}] {song['name']}")

    for song in songs:
        safe_print(f"\n처리: {song['name']}")
        try:
            create_video(song)
        except Exception as e:
            safe_print(f"  오류: {e}")

    safe_print("\n모든 영상 생성 완료!")


if __name__ == "__main__":
    main()
