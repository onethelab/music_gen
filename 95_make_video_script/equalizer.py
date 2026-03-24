"""
원형 이퀄라이저 영상 생성 모듈
- whisperX_only.py에서 사용
- whisper/moviepy 의존성 없이 독립 실행
"""

import os
import subprocess
import numpy as np
import math
from PIL import Image, ImageDraw, ImageFilter
from moviepy.config import FFMPEG_BINARY


def safe_print(text):
    try:
        print(text.encode('cp949', errors='replace').decode('cp949'))
    except Exception:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))


def generate_circular_equalizer(mp3_path, eq_video_path, duration, size=480, fps=24):
    """MP3에서 원형 막대 이퀄라이저 영상 생성 (투명 배경, 음량 반응 원 크기)"""

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
    max_bar_height = int(render_size * 0.69)
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

    safe_print(f"  이퀄라이저 생성 완료: {eq_video_path}")
    return eq_video_path
