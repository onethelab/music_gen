import subprocess, os, numpy as np
from moviepy.config import FFMPEG_BINARY

img = 'g:/project/music_gen/06_img/01_Forgotten_Cathedral_v1.png'
mp3 = 'g:/project/music_gen/05_Mp3/01_Forgotten_Cathedral_v1.mp3'
srt = 'g:/project/music_gen/95_make_video_script/srt/01_Forgotten_Cathedral_v1.srt'
eq = 'g:/project/music_gen/95_make_video_script/srt/01_Forgotten_Cathedral_v1_eq.mov'
cmd_path = 'g:/project/music_gen/bc.txt'
out = 'g:/project/music_gen/07_Video/01_Forgotten_Cathedral_v1_bloom_test.mp4'

# mp3에서 볼륨 데이터 추출
decode_cmd = [FFMPEG_BINARY, '-i', mp3, '-f', 's16le', '-ac', '1', '-ar', '44100', '-v', 'quiet', '-']
result = subprocess.run(decode_cmd, capture_output=True)
samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)

fps = 24
duration = len(samples) / 44100
num_frames = int(duration * fps)
samples_per_frame = max(1, len(samples) // num_frames)

# sendcmd: bloom sigma + opacity를 볼륨에 연동
prev_vol = 0.0
with open(cmd_path, 'w') as f:
    step = 12  # 0.5초 간격 (24fps / 12 = 2Hz)
    for i in range(0, num_frames, step):
        start = i * samples_per_frame
        end = min(start + samples_per_frame * step, len(samples))
        chunk = samples[start:end]

        rms = np.sqrt(np.mean(chunk ** 2)) / 32768.0
        rms = min(rms * 3.0, 1.0)
        vol = prev_vol * 0.7 + rms * 0.3
        prev_vol = vol

        t = i / fps
        # bloom sigma: 조용할 때 40, 볼륨 높을 때 320
        sigma = 40 + vol * 280
        # bloom opacity: 조용할 때 0.3, 볼륨 높을 때 1.0 (ffmpeg 상한 1.0)
        opacity = 0.3 + vol * 0.7
        opacity = min(opacity, 1.0)

        f.write(f"{t:.4f} [enter] gblur@bloom sigma {sigma:.2f};\n")
        f.write(f"{t:.4f} [enter] blend@bloom all_opacity {opacity:.4f};\n")

print(f'bloom cmd 생성: {num_frames} 프레임')

# ffmpeg
srt_e = srt.replace('\\', '/').replace(':', '\\:')
cmd_e = cmd_path.replace('\\', '/').replace(':', '\\:')

fc = (
    "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
    "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
    "split[a][b];"
    "[b]colorlevels=rimin=0.3:gimin=0.3:bimin=0.3,"
    "eq=brightness=0.5:contrast=5,"
    "sendcmd=f='" + cmd_e + "',"
    "gblur@bloom=sigma=20[bloom];"
    "[a][bloom]blend@bloom=all_mode=addition:all_opacity=0.3[out]"
)

print('ffmpeg 렌더링 중...')
result = subprocess.run([
    FFMPEG_BINARY,
    '-loop', '1', '-i', img,
    '-i', mp3,
    '-filter_complex', fc,
    '-map', '[out]', '-map', '1:a',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
    '-c:a', 'aac', '-b:a', '192k',
    '-t', str(duration), '-shortest', '-y', out
], capture_output=True, text=True)

if result.returncode == 0:
    print(f'완료: {out} ({os.path.getsize(out)//1024//1024} MB)')
else:
    print(f'오류: {result.stderr[-500:]}')
