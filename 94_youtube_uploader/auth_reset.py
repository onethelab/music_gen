"""
YouTube OAuth 재인증 스크립트
- 기존 토큰을 삭제하고 브라우저를 열어 새 계정으로 인증합니다.

사용법:
    python 94_youtube_uploader/auth_reset.py
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

UPLOADER_DIR = os.path.dirname(__file__)
CLIENT_SECRET_FILE = os.path.join(UPLOADER_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(UPLOADER_DIR, "token.json")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main():
    if not os.path.exists(CLIENT_SECRET_FILE):
        print("client_secret.json 파일이 없습니다.")
        sys.exit(1)

    # 기존 토큰 백업 후 삭제
    if os.path.exists(TOKEN_FILE):
        backup = TOKEN_FILE + ".bak"
        os.replace(TOKEN_FILE, backup)
        print(f"기존 토큰 백업: {backup}")

    print("브라우저에서 deeloop 채널의 Google 계정으로 로그인하세요.")
    print("=" * 50)

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=8080)

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print("=" * 50)
    print(f"새 토큰 저장 완료: {TOKEN_FILE}")
    print("인증이 완료되었습니다.")


if __name__ == "__main__":
    main()
