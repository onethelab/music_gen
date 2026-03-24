"""
유튜브 업로드 자동화
- 07_Video/*.mp4 파일을 유튜브에 업로드
- 08_youtube_script/*.md에서 제목, 설명, 태그 파싱
- 미공개(unlisted) 상태로 업로드

사용법:
    cd 94_youtube_uploader
    python youtube_upload.py

사전 준비:
    1. Google Cloud Console에서 프로젝트 생성
    2. YouTube Data API v3 활성화
    3. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)
    4. client_secret.json을 94_youtube_uploader/ 폴더에 저장
    5. 최초 실행 시 브라우저에서 Google 계정 인증 → token.json 자동 생성
"""

import os
import re
import sys
import glob

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "07_Video")
SCRIPT_DIR = os.path.join(BASE_DIR, "08_youtube_script")
COMPLETE_DIR = os.path.join(BASE_DIR, "09_complete")
UPLOADER_DIR = os.path.dirname(__file__)

CLIENT_SECRET_FILE = os.path.join(UPLOADER_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(UPLOADER_DIR, "token.json")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
DEFAULT_PLAYLIST = "deelup test"

# 음악유형별 재생목록 매핑 (유튜브 스크립트에서 장르 키워드 + 언어 감지)
PLAYLIST_RULES = [
    {"keywords": ["City Pop"], "eng": "deelup citypop eng", "kor": "deelup citypop kor", "jp": "deelup citypop jp"},
    {"keywords": ["Gothic Synthwave", "고딕 신스"], "eng": "deelup gothic synthwave eng", "kor": "deelup gothic synthwave kor"},
    {"keywords": ["Synth Indie Pop"], "eng": "deelup synth indie pop eng", "kor": "deelup synth indie pop kor"},
]
KOREAN_INDICATORS = ["Korean", "한국어", "한국", "KoreanCityPop", "Korean City Pop"]
JAPANESE_INDICATORS = ["Japanese", "日本語", "日本", "JapaneseCityPop", "Japanese City Pop", "シティポップ"]


def safe_print(text):
    """cp949 인코딩 안전 출력"""
    print(text.encode('cp949', errors='ignore').decode('cp949'))


def get_authenticated_service():
    """YouTube API 인증 및 서비스 객체 생성"""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                safe_print("client_secret.json 파일이 없습니다.")
                safe_print("")
                safe_print("설정 방법:")
                safe_print("1. https://console.cloud.google.com 접속")
                safe_print("2. 프로젝트 생성 또는 선택")
                safe_print("3. API 및 서비스 > 라이브러리 > 'YouTube Data API v3' 활성화")
                safe_print("4. API 및 서비스 > 사용자 인증 정보 > OAuth 2.0 클라이언트 ID 생성")
                safe_print("   - 애플리케이션 유형: 데스크톱 앱")
                safe_print("5. JSON 다운로드 → 94_youtube_uploader/client_secret.json으로 저장")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def parse_youtube_script(filepath):
    """유튜브 스크립트 md 파일에서 제목, 설명, 태그 추출"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    title = ""
    description = ""
    tags = []

    title_match = re.search(r'## 유튜브 제목\s*\n(.+)', content)
    if title_match:
        title = title_match.group(1).strip()

    desc_match = re.search(
        r'## 유튜브 설명\s*\n([\s\S]+?)(?=\n## 태그|\Z)', content
    )
    if desc_match:
        description = desc_match.group(1).strip()

    tags_match = re.search(r'## 태그\s*\n(.+)', content)
    if tags_match:
        tags = [t.strip() for t in tags_match.group(1).split(',') if t.strip()]

    return title, description, tags, content


def find_upload_targets():
    """업로드할 영상 목록 생성 (mp4 + 스크립트 매칭, v1/v2 개별)"""
    video_files = sorted(glob.glob(os.path.join(VIDEO_DIR, "*.mp4")))
    targets = []

    os.makedirs(COMPLETE_DIR, exist_ok=True)

    for video_path in video_files:
        basename = os.path.splitext(os.path.basename(video_path))[0]

        # 완료 파일이 있으면 건너뛰기
        complete_file = os.path.join(COMPLETE_DIR, f"{basename}.md")
        if os.path.exists(complete_file):
            safe_print(f"  [{basename}] 이미 업로드 완료 (건너뜁니다)")
            continue

        # v1/v2 버전 감지 (예: "05_Gangnam_Lovers_Fight_v1" → base="05_Gangnam_Lovers_Fight", version="V1")
        version_match = re.match(r'^(.+)_(v\d+)$', basename)
        if version_match:
            base_name = version_match.group(1)
            version_label = version_match.group(2).upper()  # "v1" → "V1"
        else:
            base_name = basename
            version_label = None

        # 스크립트는 base_name으로 찾기 (v1/v2 공유)
        script_path = os.path.join(SCRIPT_DIR, f"{base_name}.md")

        if os.path.exists(script_path):
            title, description, tags, script_content = parse_youtube_script(script_path)
        else:
            title = base_name.replace('_', ' ')
            description = ""
            tags = []
            script_content = ""

        # 재생목록 결정 (장르 키워드 + 언어 감지)
        playlist_name = DEFAULT_PLAYLIST
        playlist_privacy = 'public'
        for rule in PLAYLIST_RULES:
            if any(kw in script_content for kw in rule["keywords"]):
                is_korean = any(ind in script_content for ind in KOREAN_INDICATORS)
                is_japanese = any(ind in script_content for ind in JAPANESE_INDICATORS)
                if is_korean:
                    playlist_name = rule["kor"]
                elif is_japanese and "jp" in rule:
                    playlist_name = rule["jp"]
                else:
                    playlist_name = rule["eng"]
                break

        # 제목에 V1/V2 추가
        if version_label:
            title = f"{title} {version_label}"

        targets.append({
            'name': basename,
            'video': video_path,
            'script': script_path if os.path.exists(script_path) else None,
            'title': title,
            'description': description,
            'tags': tags,
            'playlist': playlist_name,
            'playlist_privacy': playlist_privacy,
        })

    return targets


def upload_video(youtube, target):
    """유튜브에 영상 업로드 (미공개)"""
    body = {
        'snippet': {
            'title': target['title'],
            'description': target['description'],
            'tags': target['tags'],
            'categoryId': '10',  # Music
        },
        'status': {
            'privacyStatus': 'unlisted',
            'selfDeclaredMadeForKids': False,
        },
    }

    media = MediaFileUpload(
        target['video'],
        mimetype='video/mp4',
        resumable=True,
        chunksize=1024 * 1024,
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
    safe_print(f"  업로드 완료: {video_url}")
    return video_url


def get_playlist_id(youtube, playlist_name, playlist_cache, privacy='public'):
    """재생목록 ID를 캐시에서 찾거나, YouTube에서 찾거나, 새로 생성"""
    if playlist_name in playlist_cache:
        return playlist_cache[playlist_name]

    # 기존 재생목록 검색
    request = youtube.playlists().list(part='snippet', mine=True, maxResults=50)
    response = request.execute()

    for item in response.get('items', []):
        name = item['snippet']['title']
        playlist_cache[name] = item['id']
        if name == playlist_name:
            safe_print(f"  재생목록 발견: {playlist_name} (ID: {item['id']})")

    if playlist_name in playlist_cache:
        return playlist_cache[playlist_name]

    # 없으면 새로 생성
    body = {
        'snippet': {
            'title': playlist_name,
            'description': f'{playlist_name} - AI 생성 음악',
        },
        'status': {
            'privacyStatus': privacy,
        },
    }
    response = youtube.playlists().insert(part='snippet,status', body=body).execute()
    playlist_id = response['id']
    playlist_cache[playlist_name] = playlist_id
    safe_print(f"  재생목록 생성: {playlist_name} (ID: {playlist_id}, {privacy})")
    return playlist_id


def add_to_playlist(youtube, playlist_id, video_id, playlist_name):
    """영상을 재생목록에 추가"""
    body = {
        'snippet': {
            'playlistId': playlist_id,
            'resourceId': {
                'kind': 'youtube#video',
                'videoId': video_id,
            },
        },
    }
    youtube.playlistItems().insert(part='snippet', body=body).execute()
    safe_print(f"  재생목록 추가 완료: {playlist_name}")


def main():
    targets = find_upload_targets()
    if not targets:
        safe_print("07_Video에 업로드할 mp4 파일이 없습니다.")
        return

    safe_print(f"\n업로드 대상:")
    for i, t in enumerate(targets):
        script_status = "스크립트 있음" if t['script'] else "스크립트 없음"
        safe_print(f"  [{i}] {t['name']} ({script_status})")
        safe_print(f"      제목: {t['title']}")

    safe_print("")
    youtube = get_authenticated_service()

    # 재생목록 캐시 (이름 → ID)
    playlist_cache = {}

    # 사용될 재생목록 미리 표시
    playlist_names = sorted(set(t['playlist'] for t in targets))
    safe_print(f"  재생목록 추가: {', '.join(playlist_names)}")

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
            playlist_id = get_playlist_id(youtube, t['playlist'], playlist_cache, t.get('playlist_privacy', 'public'))
            add_to_playlist(youtube, playlist_id, video_id, t['playlist'])

            # 완료 파일 생성
            complete_file = os.path.join(COMPLETE_DIR, f"{t['name']}.md")
            with open(complete_file, 'w', encoding='utf-8') as f:
                from datetime import datetime
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
