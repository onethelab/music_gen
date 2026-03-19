"""
재생목록 간 영상 이동 스크립트
- AiDeer Ready에서 삭제
- Baby Lullaby에 추가
"""
import os
import sys

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

UPLOADER_DIR = os.path.dirname(__file__)
TOKEN_FILE = os.path.join(UPLOADER_DIR, "token.json")
CLIENT_SECRET_FILE = os.path.join(UPLOADER_DIR, "client_secret.json")
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# 이동할 video ID 목록
VIDEO_IDS = [
    "kaXztdDStqM",  # 24_Moon_Bunny_v1
    "_en9HL7m6jE",  # 24_Moon_Bunny_v2
    "CgKvSNkTntM",  # 25_Star_Counting_Night_v1
    "f41ufvmmpf8",  # 25_Star_Counting_Night_v2
    "qMSyLoveVSc",  # 26_Cloud_Sheep_v1
    "8twQaklq_0o",  # 26_Cloud_Sheep_v2
    "TzxmmBQyNSI",  # 27_Dream_Train_v1
    "CMUkCvKt_-c",  # 27_Dream_Train_v2
    "67xuApRMCI0",  # 28_Mama_Heartbeat_v1
    "6TEI9KXnC2E",  # 28_Mama_Heartbeat_v2
    "KKqaPKQSS3k",  # 29_Butterfly_Nap_v1
    "I1XkhiSie2o",  # 29_Butterfly_Nap_v2
]

SOURCE_PLAYLIST = "AiDeer Ready"
TARGET_PLAYLIST = "Baby Lullaby"


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


def find_playlist_id(youtube, name):
    request = youtube.playlists().list(part='snippet', mine=True, maxResults=50)
    response = request.execute()
    for item in response.get('items', []):
        if item['snippet']['title'] == name:
            return item['id']
    return None


def create_playlist(youtube, name):
    body = {
        'snippet': {'title': name, 'description': f'{name} - AI 생성 음악'},
        'status': {'privacyStatus': 'public'},
    }
    response = youtube.playlists().insert(part='snippet,status', body=body).execute()
    return response['id']


def get_playlist_items(youtube, playlist_id):
    """재생목록의 모든 아이템 조회 (playlistItemId + videoId)"""
    items = []
    next_page = None
    while True:
        request = youtube.playlistItems().list(
            part='snippet',
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page,
        )
        response = request.execute()
        for item in response.get('items', []):
            vid = item['snippet']['resourceId']['videoId']
            items.append({'playlistItemId': item['id'], 'videoId': vid})
        next_page = response.get('nextPageToken')
        if not next_page:
            break
    return items


def main():
    youtube = get_authenticated_service()

    # 1. 소스 재생목록 찾기
    source_id = find_playlist_id(youtube, SOURCE_PLAYLIST)
    if not source_id:
        safe_print(f"소스 재생목록 '{SOURCE_PLAYLIST}'을 찾을 수 없습니다.")
        return
    safe_print(f"소스 재생목록: {SOURCE_PLAYLIST} (ID: {source_id})")

    # 2. 타겟 재생목록 찾기 또는 생성
    target_id = find_playlist_id(youtube, TARGET_PLAYLIST)
    if target_id:
        safe_print(f"타겟 재생목록 발견: {TARGET_PLAYLIST} (ID: {target_id})")
    else:
        target_id = create_playlist(youtube, TARGET_PLAYLIST)
        safe_print(f"타겟 재생목록 생성: {TARGET_PLAYLIST} (ID: {target_id})")

    # 3. 소스 재생목록에서 해당 영상 삭제
    safe_print(f"\n--- {SOURCE_PLAYLIST}에서 삭제 ---")
    source_items = get_playlist_items(youtube, source_id)
    video_id_set = set(VIDEO_IDS)
    removed = 0
    for item in source_items:
        if item['videoId'] in video_id_set:
            youtube.playlistItems().delete(id=item['playlistItemId']).execute()
            safe_print(f"  삭제: {item['videoId']}")
            removed += 1
    safe_print(f"  총 {removed}개 삭제 완료")

    # 4. 타겟 재생목록에 추가
    safe_print(f"\n--- {TARGET_PLAYLIST}에 추가 ---")
    added = 0
    for vid in VIDEO_IDS:
        body = {
            'snippet': {
                'playlistId': target_id,
                'resourceId': {'kind': 'youtube#video', 'videoId': vid},
            },
        }
        youtube.playlistItems().insert(part='snippet', body=body).execute()
        safe_print(f"  추가: {vid}")
        added += 1
    safe_print(f"  총 {added}개 추가 완료")

    safe_print(f"\n완료! {removed}개 삭제 → {added}개 추가")


if __name__ == "__main__":
    main()
