"""
mp3 + png → mp4 변환 자동화 (v1/v2 개별 생성)
- 05_Mp3/의 v1, v2 mp3 각각에 대해
- 06_img/의 대응하는 v1, v2 이미지를 배경으로 mp4 생성
- 원형 이퀄라이저 + 배경 밝기 펄스 포함 (자막 없음)
- 07_Video/에 저장

사용법:
    cd 93_make_video
    python video_create.py                  # 전체
    python video_create.py 14_Chrome_Highway  # 특정 곡
"""

import os
import sys
import glob
import re
import subprocess
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from moviepy import AudioFileClip
from moviepy.config import FFMPEG_BINARY

_ffmpeg_dir = os.path.dirname(FFMPEG_BINARY)
if _ffmpeg_dir not in os.environ.get('PATH', ''):
    os.environ['PATH'] = _ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
IMG_DIR = os.path.join(BASE_DIR, "06_img")
MP3_DIR = os.path.join(BASE_DIR, "05_Mp3")
VIDEO_DIR = os.path.join(BASE_DIR, "07_Video")
EQ_DIR = os.path.join(BASE_DIR, "93_make_video", "eq_cache")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
VIDEO_FPS = 24


def safe_print(text):
    """cp949 인코딩 안전 출력"""
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def find_songs(filter_name=None):
    """05_Mp3/에서 mp3 파일을 기준으로 v1/v2 개별 곡 목록 생성"""
    mp3_files = sorted(glob.glob(os.path.join(MP3_DIR, "*_v*.mp3")))
    songs = []

    for mp3_path in mp3_files:
        mp3_basename = os.path.splitext(os.path.basename(mp3_path))[0]
        version_match = re.match(r'^(.+)_(v\d+)$', mp3_basename)
        if not version_match:
            continue

        base_name = version_match.group(1)
        version = version_match.group(2)

        # 필터 적용
        if filter_name and filter_name not in base_name:
            continue

        # 해당 버전의 이미지 찾기
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
    if os.path.exists(eq_video_path):
        safe_print(f"  이퀄라이저 이미 존재: {os.path.basename(eq_video_path)}")
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
    num_bars = 48
    cx = render_size // 2
    cy = render_size // 2
    base_inner_radius = int(render_size * 0.18)
    max_bar_height = int(render_size * 0.69)
    bar_width = 4
    radius_scale_range = 0.30

    # FFmpeg process to encode equalizer video
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
    volume_data = []

    for i in range(num_frames):
        start = i * samples_per_frame
        end = min(start + samples_per_frame, len(samples))
        chunk = samples[start:end]

        if len(chunk) < 512:
            chunk = np.pad(chunk, (0, 512 - len(chunk)))

        rms = np.sqrt(np.mean(chunk ** 2)) / 32768.0
        rms = min(rms * 3.0, 1.0)
        volume = prev_volume * 0.7 + rms * 0.3
        prev_volume = volume

        inner_radius = int(base_inner_radius * (1.0 + radius_scale_range * volume))
        volume_data.append(volume)

        window = np.hanning(len(chunk))
        fft_data = np.abs(np.fft.rfft(chunk * window))

        freqs = np.fft.rfftfreq(len(chunk), 1.0 / sample_rate)
        log_bands = np.logspace(np.log10(30), np.log10(15000), num_bars + 1)
        magnitudes = np.zeros(num_bars)
        for j in range(num_bars):
            mask = (freqs >= log_bands[j]) & (freqs < log_bands[j + 1])
            if mask.any():
                magnitudes[j] = np.mean(fft_data[mask])

        max_mag = magnitudes.max()
        if max_mag > 0:
            magnitudes = magnitudes / max_mag

        magnitudes = prev_mags * 0.3 + magnitudes * 0.7
        prev_mags = magnitudes.copy()

        # 보간: 48개 → 96개
        interp_mags = np.zeros(num_bars * 2)
        for j in range(num_bars):
            interp_mags[j * 2] = magnitudes[j]
            interp_mags[j * 2 + 1] = (magnitudes[j] + magnitudes[(j + 1) % num_bars]) / 2.0
        render_num_bars = num_bars * 2

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

        ring_r = inner_radius - 2
        bar_draw.ellipse(
            [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
            outline=(255, 255, 255, 100), width=1
        )

        # 블랙 글로우
        black_glow = bar_layer.copy()
        r, g, b, a = black_glow.split()
        black_glow = Image.merge('RGBA', (
            Image.new('L', (render_size, render_size), 0),
            Image.new('L', (render_size, render_size), 0),
            Image.new('L', (render_size, render_size), 0),
            a,
        ))
        black_glow = black_glow.filter(ImageFilter.GaussianBlur(radius=24))
        r, g, b, a = black_glow.split()
        a = a.point(lambda x: x // 2)
        black_glow = Image.merge('RGBA', (r, g, b, a))

        # 화이트 글로우
        white_glow = bar_layer.filter(ImageFilter.GaussianBlur(radius=12))

        # 합성
        img = Image.new('RGBA', (render_size, render_size), (0, 0, 0, 0))
        img = Image.alpha_composite(img, black_glow)
        img = Image.alpha_composite(img, white_glow)
        img = Image.alpha_composite(img, bar_layer)

        img = img.resize((size, size), Image.LANCZOS)

        proc.stdin.write(img.tobytes())

        if (i + 1) % (fps * 30) == 0:
            safe_print(f"    이퀄라이저 렌더링: {i + 1}/{num_frames} 프레임")

    proc.stdin.close()
    proc.wait()

    # 배경 밝기 펄스 sendcmd 파일 생성
    cmd_path = eq_video_path.replace('.mov', '_cmd.txt')
    with open(cmd_path, 'w') as f:
        for idx, vol in enumerate(volume_data):
            t = idx / fps
            brightness = 0.15 * vol
            gamma = 1.0 - 0.4 * vol
            f.write(f"{t:.4f} [enter] eq@bgpulse brightness {brightness:.4f};\n")
            f.write(f"{t:.4f} [enter] eq@bgpulse gamma {gamma:.4f};\n")
    safe_print(f"  배경 펄스 커맨드 생성 완료")

    safe_print(f"  이퀄라이저 생성 완료: {os.path.basename(eq_video_path)}")
    return eq_video_path


def create_video(song):
    """이미지 + mp3 → mp4 생성 (이퀄라이저 + 배경펄스 포함, 자막 없음)"""
    img_path = song['img']
    mp3_path = song['mp3']
    output_path = os.path.join(VIDEO_DIR, f"{song['name']}.mp4")

    if os.path.exists(output_path):
        safe_print(f"  이미 존재: {output_path} (건너뜁니다)")
        return output_path

    safe_print(f"  이미지: {os.path.basename(img_path)}")
    safe_print(f"  오디오: {os.path.basename(mp3_path)}")

    duration = get_audio_duration(mp3_path)
    if duration is None:
        audio = AudioFileClip(mp3_path)
        duration = audio.duration
        audio.close()

    safe_print(f"  오디오 길이: {duration:.1f}초 ({duration/60:.1f}분)")

    # 원형 이퀄라이저 영상 생성
    eq_video_path = os.path.join(EQ_DIR, f"{song['name']}_eq.mov")
    generate_circular_equalizer(mp3_path, eq_video_path, duration)

    # sendcmd 파일 경로
    cmd_file = eq_video_path.replace('.mov', '_cmd.txt').replace('\\', '/').replace(':', '\\:')

    safe_print("  mp4 생성 중 (이퀄라이저 + 배경펄스 포함)...")
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
    safe_print(f"  저장 완료: {output_path} ({file_size // (1024*1024)} MB)")
    return output_path


def main():
    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(EQ_DIR, exist_ok=True)

    # 커맨드라인 인자로 특정 곡 필터링
    filter_name = sys.argv[1] if len(sys.argv) > 1 else None

    songs = find_songs(filter_name)
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
