"""
여러 MP4 파일을 하나로 합치는 스크립트
사용법: python merge_mp4.py [--output 출력파일명]

mp4_list.txt에 합칠 파일 경로를 순서대로 작성하면 해당 순서로 합침.
mp4_list.txt가 없으면 자동 생성됨.
"""

import subprocess
import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "11_playlist_video"
MP4_LIST_FILE = SCRIPT_DIR / "mp4_list.txt"
DEFAULT_OUTPUT = OUTPUT_DIR / "output_merged.mp4"
FFMPEG_CONCAT_FILE = SCRIPT_DIR / "_concat_list.txt"


def create_sample_list():
    """mp4_list.txt 샘플 파일 생성"""
    sample = """# 합칠 MP4 파일 경로를 한 줄에 하나씩 작성 (순서대로 합쳐짐)
# '#'으로 시작하는 줄은 무시됨
# 예시:
# G:/project/music_gen/07_Video/14_Chrome_Highway_v1.mp4
# G:/project/music_gen/07_Video/15_Derelict_Protocol_v1.mp4
"""
    MP4_LIST_FILE.write_text(sample, encoding="utf-8")
    print(f"[생성] {MP4_LIST_FILE}")
    print("파일 경로를 작성한 후 다시 실행하세요.")


def read_mp4_list():
    """mp4_list.txt에서 파일 목록 읽기"""
    if not MP4_LIST_FILE.exists():
        create_sample_list()
        return []

    files = []
    for line in MP4_LIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line)
        if not p.exists():
            print(f"[경고] 파일 없음: {p}")
            continue
        files.append(p)
    return files


def create_concat_file(mp4_files):
    """ffmpeg concat용 파일 생성"""
    lines = []
    for f in mp4_files:
        safe_path = str(f.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    FFMPEG_CONCAT_FILE.write_text("\n".join(lines), encoding="utf-8")


def merge(mp4_files, output_path):
    """ffmpeg concat demuxer로 MP4 합치기"""
    create_concat_file(mp4_files)

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(FFMPEG_CONCAT_FILE),
        "-c", "copy",
        str(output_path),
    ]

    print(f"\n[합치기] {len(mp4_files)}개 → {output_path}")
    for i, f in enumerate(mp4_files, 1):
        print(f"  {i:2d}. {f.name}")

    print()
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("[실패] ffmpeg 오류:")
        print(result.stderr)
        return False

    FFMPEG_CONCAT_FILE.unlink(missing_ok=True)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[완료] {output_path} ({size_mb:.1f} MB)")
    return True


def main():
    output_path = DEFAULT_OUTPUT

    args = sys.argv[1:]
    if "--output" in args:
        idx = args.index("--output")
        if idx + 1 < len(args):
            output_path = Path(args[idx + 1])

    mp4_files = read_mp4_list()
    if not mp4_files:
        print("[오류] 합칠 MP4 파일이 없습니다.")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merge(mp4_files, output_path)


if __name__ == "__main__":
    main()
