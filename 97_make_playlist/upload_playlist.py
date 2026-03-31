"""
플레이리스트 합본 영상 YouTube 업로드
- 94_youtube_uploader의 인증 토큰 재사용
- 53_playlist/gothic_synthwave_1hour.md의 업로드 스크립트 섹션에서 메타데이터 파싱

사용법: python upload_playlist.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "94_youtube_uploader"))

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
UPLOADER_DIR = os.path.join(BASE_DIR, "94_youtube_uploader")
CLIENT_SECRET_FILE = os.path.join(UPLOADER_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(UPLOADER_DIR, "token.json")

VIDEO_PATH = os.path.join(BASE_DIR, "11_playlist_video", "gothic_synthwave_1hour.mp4")
THUMBNAIL_PATH = os.path.join(BASE_DIR, "12_playlist_thumbnail", "gothic_synthwave_1hour_thumbnail.png")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

TITLE = "Gothic Synthwave Instrumental Mix 1 Hour | 고딕 신스웨이브 인스트루멘탈 1시간 | AI Generated Music"

DESCRIPTION = """🎧 Gothic Synthwave Instrumental Mix — 1 Hour of Dark Synth Atmosphere
고딕 신스웨이브 인스트루멘탈 믹스 1시간

Dark, driving, and atmospheric — 16 tracks of gothic synthwave instrumentals for focus, coding, late-night drives, or just vibing in the dark.

🕐 Tracklist / 타임라인:
0:00 철골 숲 Steel Forest V1
2:23 크롬 하이웨이 Chrome Highway V1
5:32 폐허의 프로토콜 Derelict Protocol V1
8:46 죽은 신호 Dead Signal V1
12:54 블랙아웃 체이스 Blackout Chase V1
17:19 녹슨 궤도 Rusted Orbit V2
21:34 터미널 루프 Terminal Loop V1
25:58 철의 하강 Iron Descent V1
30:01 네온 출혈 Neon Hemorrhage V1
34:00 철골 숲 Steel Forest V2
37:20 크롬 하이웨이 Chrome Highway V2
40:02 폐허의 프로토콜 Derelict Protocol V2
43:37 블랙아웃 체이스 Blackout Chase V2
47:27 죽은 신호 Dead Signal V2
51:27 네온 출혈 Neon Hemorrhage V2
55:26 철의 하강 Iron Descent V2

🔗 Individual Tracks / 개별 곡 링크:
• 철골 숲 Steel Forest V1: https://youtu.be/vGKUJw9n1YY
• 크롬 하이웨이 Chrome Highway V1: https://youtu.be/YLA3d9Ql7MA
• 폐허의 프로토콜 Derelict Protocol V1: https://youtu.be/-NJiK3lgCCI
• 죽은 신호 Dead Signal V1: https://youtu.be/glItRVInhjE
• 블랙아웃 체이스 Blackout Chase V1: https://youtu.be/0rKy3snRzSs
• 녹슨 궤도 Rusted Orbit V2: https://youtu.be/1nHJifMOFWg
• 터미널 루프 Terminal Loop V1: https://youtu.be/TuXAACYyxVc
• 철의 하강 Iron Descent V1: https://youtu.be/lrx3qE7kCi4
• 네온 출혈 Neon Hemorrhage V1: https://youtu.be/_Q4IT31BUmA
• 철골 숲 Steel Forest V2: https://youtu.be/YGKVK3Q2Ypg
• 크롬 하이웨이 Chrome Highway V2: https://youtu.be/iNL7YOUuM9A
• 폐허의 프로토콜 Derelict Protocol V2: https://youtu.be/z1WEG78gQ3s
• 블랙아웃 체이스 Blackout Chase V2: https://youtu.be/fF8oEsLtETQ
• 죽은 신호 Dead Signal V2: https://youtu.be/gH6z4wWcpUY
• 네온 출혈 Neon Hemorrhage V2: https://youtu.be/u3DkVDbobmQ
• 철의 하강 Iron Descent V2: https://youtu.be/tuV9YPGblUY

#GothicSynthwave #DarkSynthwave #Instrumental #1HourMix #SynthwaveMix #DarkSynth #AIMusic #고딕신스웨이브 #신스웨이브 #인스트루멘탈 #1시간 #deeloop #deelup"""

TAGS = [
    "gothic synthwave", "dark synthwave", "synthwave instrumental",
    "1 hour mix", "synthwave mix", "dark synth", "darkwave",
    "instrumental mix", "AI generated music", "고딕 신스웨이브",
    "신스웨이브 믹스", "인스트루멘탈", "1시간", "deeloop", "deelup",
    "study music", "coding music", "dark ambient", "cyberpunk music",
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


def upload_video(youtube):
    safe_print(f"\n[업로드 시작]")
    safe_print(f"  파일: {VIDEO_PATH}")
    safe_print(f"  제목: {TITLE}")
    safe_print(f"  상태: 일부공개(unlisted)")

    body = {
        'snippet': {
            'title': TITLE,
            'description': DESCRIPTION,
            'tags': TAGS,
            'categoryId': '10',
        },
        'status': {
            'privacyStatus': 'unlisted',
            'selfDeclaredMadeForKids': False,
        },
    }

    media = MediaFileUpload(
        VIDEO_PATH,
        mimetype='video/mp4',
        resumable=True,
        chunksize=1024 * 1024 * 5,
    )

    request = youtube.videos().insert(
        part='snippet,status',
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            safe_print(f"  업로드 중... {progress}%")

    video_id = response['id']
    video_url = f"https://youtu.be/{video_id}"
    safe_print(f"\n[업로드 완료] {video_url}")
    return video_id, video_url


def upload_thumbnail(youtube, video_id):
    if not os.path.exists(THUMBNAIL_PATH):
        safe_print(f"  썸네일 파일 없음: {THUMBNAIL_PATH}")
        return

    try:
        media = MediaFileUpload(THUMBNAIL_PATH, mimetype='image/png')
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=media,
        ).execute()
        safe_print(f"[썸네일 설정 완료]")
    except Exception as e:
        safe_print(f"[썸네일 설정 실패] {e}")


def main():
    if not os.path.exists(VIDEO_PATH):
        safe_print(f"[오류] 영상 파일 없음: {VIDEO_PATH}")
        return

    youtube = get_authenticated_service()
    video_id, video_url = upload_video(youtube)
    upload_thumbnail(youtube, video_id)

    safe_print(f"\n=== 결과 ===")
    safe_print(f"  URL: {video_url}")
    safe_print(f"  상태: 일부공개")
    safe_print(f"  썸네일: {THUMBNAIL_PATH}")


if __name__ == "__main__":
    main()
