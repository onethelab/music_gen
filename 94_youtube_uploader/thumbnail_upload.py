"""
유튜브 썸네일 업로드
- 09_complete/*.md에서 video ID 추출
- 10_thumbnail/*.png 매칭하여 썸네일 설정

사용법:
    cd 94_youtube_uploader
    python thumbnail_upload.py                    # 전체
    python thumbnail_upload.py 11_Drift           # 특정 곡만
"""

import os
import re
import sys
import glob
import time

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
COMPLETE_DIR = os.path.join(BASE_DIR, "09_complete")
THUMB_DIR = os.path.join(BASE_DIR, "10_thumbnail")
UPLOADER_DIR = os.path.dirname(__file__)

CLIENT_SECRET_FILE = os.path.join(UPLOADER_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(UPLOADER_DIR, "token.json")

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def safe_print(text):
    print(text.encode('cp949', errors='ignore').decode('cp949'))


def get_authenticated_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def main():
    filter_name = sys.argv[1] if len(sys.argv) > 1 else None

    youtube = get_authenticated_service()

    complete_files = sorted(glob.glob(os.path.join(COMPLETE_DIR, "*.md")))
    results = []

    for cfile in complete_files:
        basename = os.path.splitext(os.path.basename(cfile))[0]

        if filter_name and filter_name not in basename:
            continue

        thumb_path = os.path.join(THUMB_DIR, f"{basename}.png")
        if not os.path.exists(thumb_path):
            continue

        with open(cfile, 'r', encoding='utf-8') as f:
            content = f.read()
        url_match = re.search(r'YouTube URL:\s*https://youtu\.be/(\S+)', content)
        if not url_match:
            continue
        video_id = url_match.group(1)

        safe_print(f"  {basename} -> {video_id} ... ")
        try:
            media = MediaFileUpload(thumb_path, mimetype='image/png')
            youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
            safe_print(f"    OK")
            results.append((basename, "OK"))
            time.sleep(2)
        except Exception as e:
            safe_print(f"    FAIL: {e}")
            results.append((basename, f"FAIL: {e}"))

    safe_print(f"\n=== 결과 ===")
    ok = sum(1 for _, s in results if s == "OK")
    fail = len(results) - ok
    safe_print(f"  성공: {ok}, 실패: {fail}")
    for name, status in results:
        if status != "OK":
            safe_print(f"  {name}: {status}")


if __name__ == "__main__":
    main()
