"""
기존 YouTube 영상 삭제 → 완료 기록 삭제 → youtube_upload.py 로직으로 재업로드
"""

import os
import sys

# youtube_upload.py와 같은 디렉토리
sys.path.insert(0, os.path.dirname(__file__))

from youtube_upload import (
    get_authenticated_service,
    find_upload_targets,
    upload_video,
    get_playlist_id,
    add_to_playlist,
    safe_print,
    COMPLETE_DIR,
)
from datetime import datetime

# 삭제할 영상 ID 목록
DELETE_TARGETS = {
    "01_Forgotten_Cathedral_v1": "2Dej-G-mY_Q",
    "01_Forgotten_Cathedral_v2": "TN1SjgFda14",
    "02_Before_the_Neon_Dies_v1": "7gP6CdKXwsc",
    "02_Before_the_Neon_Dies_v2": "vFI2A8LV_Lo",
    "04_Last_Train_Cassette_v1": "_caLzrppVMU",
    "04_Last_Train_Cassette_v2": "8ci1yl2I18Y",
}


def main():
    youtube = get_authenticated_service()

    # 1. 기존 YouTube 영상 삭제
    safe_print("\n=== 기존 영상 삭제 ===")
    for name, video_id in DELETE_TARGETS.items():
        try:
            youtube.videos().delete(id=video_id).execute()
            safe_print(f"  삭제 완료: {name} ({video_id})")
        except Exception as e:
            safe_print(f"  삭제 실패: {name} ({video_id}) - {e}")

    # 2. 완료 기록 삭제
    safe_print("\n=== 완료 기록 삭제 ===")
    for name in DELETE_TARGETS:
        complete_file = os.path.join(COMPLETE_DIR, f"{name}.md")
        if os.path.exists(complete_file):
            os.remove(complete_file)
            safe_print(f"  삭제: {complete_file}")

    # 3. 재업로드 (youtube_upload.py 로직 재사용)
    safe_print("\n=== 새 영상 업로드 ===")
    targets = find_upload_targets()

    # 6곡만 필터
    target_names = set(DELETE_TARGETS.keys())
    targets = [t for t in targets if t['name'] in target_names]

    if not targets:
        safe_print("업로드할 대상이 없습니다.")
        return

    for t in targets:
        safe_print(f"  대상: {t['name']} → {t['title']}")

    playlist_cache = {}
    results = []

    for t in targets:
        safe_print(f"\n업로드: {t['name']}")
        safe_print(f"  제목: {t['title']}")
        safe_print(f"  상태: 미공개(unlisted)")
        try:
            url = upload_video(youtube, t)
            results.append((t['name'], url))

            # 재생목록에 추가
            video_id = url.split('/')[-1]
            playlist_id = get_playlist_id(
                youtube, t['playlist'], playlist_cache,
                t.get('playlist_privacy', 'public')
            )
            add_to_playlist(youtube, playlist_id, video_id, t['playlist'])

            # 완료 파일 생성
            complete_file = os.path.join(COMPLETE_DIR, f"{t['name']}.md")
            with open(complete_file, 'w', encoding='utf-8') as f:
                f.write(f"# {t['title']}\n\n")
                f.write(f"- 업로드일: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
                f.write(f"- YouTube URL: {url}\n")
                f.write(f"- 공개 상태: unlisted\n")
                f.write(f"- 재생목록: {t['playlist']}\n")
            safe_print(f"  완료 기록: {complete_file}")
        except Exception as e:
            safe_print(f"  오류: {e}")
            results.append((t['name'], f"실패: {e}"))

    safe_print("\n\n=== 업로드 결과 ===")
    for name, url in results:
        safe_print(f"  {name}: {url}")


if __name__ == "__main__":
    main()
