import subprocess, os
from moviepy.config import FFMPEG_BINARY

img = 'g:/project/music_gen/06_img/01_Forgotten_Cathedral_v1.png'
mp3 = 'g:/project/music_gen/05_Mp3/01_Forgotten_Cathedral_v1.mp3'
eq = 'g:/project/music_gen/95_make_video_script/srt/01_Forgotten_Cathedral_v1_eq.mov'
srt = 'g:/project/music_gen/95_make_video_script/srt/01_Forgotten_Cathedral_v1.srt'
cmd = 'g:/project/music_gen/95_make_video_script/srt/01_Forgotten_Cathedral_v1_eq_cmd_v4.txt'
out = 'g:/project/music_gen/07_Video/01_Forgotten_Cathedral_v1_pulse_v4.mp4'

srt_e = srt.replace('\\', '/').replace(':', '\\:')
cmd_e = cmd.replace('\\', '/').replace(':', '\\:')

fc = (
    "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
    "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
    "sendcmd=f='" + cmd_e + "',"
    "eq@bgpulse=brightness=0:gamma=1:contrast=1[bg];"
    "[bg][2:v]overlay=(W-w)/2:(H-h)/2,"
    "subtitles='" + srt_e + "':force_style='"
    "FontName=Malgun Gothic Bold,"
    "FontSize=26,"
    "PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,"
    "BackColour=&H80000000,"
    "BorderStyle=4,"
    "Outline=1,"
    "Shadow=0,"
    "MarginV=40,"
    "Alignment=2"
    "'[out]"
)

result = subprocess.run([
    FFMPEG_BINARY,
    '-loop', '1', '-i', img,
    '-i', mp3, '-i', eq,
    '-filter_complex', fc,
    '-map', '[out]', '-map', '1:a',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
    '-c:a', 'aac', '-b:a', '192k',
    '-t', '212.7', '-shortest', '-y', out
], capture_output=True, text=True)

if result.returncode == 0:
    print(f'완료: {out} ({os.path.getsize(out)//1024//1024} MB)')
else:
    print(f'오류: {result.stderr[-500:]}')
