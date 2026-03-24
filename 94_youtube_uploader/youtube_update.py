"""
유튜브 영상 메타데이터 업데이트
- 09_complete/*.md에서 video ID 추출
- 08_youtube_script/*.md에서 제목, 설명, 태그 파싱
- YouTube API로 제목/설명/태그 업데이트

사용법:
    cd 94_youtube_uploader
    python youtube_update.py                          # 전체 업데이트 (변경 감지)
    python youtube_update.py 02_Before_the_Neon_Dies  # 특정 곡만 업데이트
"""

import os
import re
import sys

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
SCRIPT_DIR = os.path.join(BASE_DIR, "08_youtube_script")
COMPLETE_DIR = os.path.join(BASE_DIR, "09_complete")
UPLOADER_DIR = os.path.dirname(__file__)

CLIENT_SECRET_FILE = os.path.join(UPLOADER_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(UPLOADER_DIR, "token.json")

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


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

    return title, description, tags


def parse_complete_file(filepath):
    """완료 파일에서 video ID 추출"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    url_match = re.search(r'YouTube URL:\s*https://youtu\.be/(\S+)', content)
    if url_match:
        return url_match.group(1)
    return None


def find_update_targets(filter_name=None):
    """업데이트 대상 목록 생성 (complete 파일 + 스크립트 매칭)"""
    import glob
    complete_files = sorted(glob.glob(os.path.join(COMPLETE_DIR, "*.md")))
    targets = []

    for complete_path in complete_files:
        basename = os.path.splitext(os.path.basename(complete_path))[0]

        # 필터가 있으면 해당 곡만
        if filter_name and filter_name not in basename:
            continue

        video_id = parse_complete_file(complete_path)
        if not video_id:
            continue

        # v1/v2 버전 감지
        version_match = re.match(r'^(.+)_(v\d+)$', basename)
        if version_match:
            base_name = version_match.group(1)
            version_label = version_match.group(2).upper()
        else:
            base_name = basename
            version_label = None

        # 스크립트에서 메타데이터 읽기
        script_path = os.path.join(SCRIPT_DIR, f"{base_name}.md")
        if not os.path.exists(script_path):
            safe_print(f"  [{basename}] 스크립트 없음 (건너뜁니다)")
            continue

        title, description, tags = parse_youtube_script(script_path)

        if version_label:
            title = f"{title} {version_label}"

        targets.append({
            'name': basename,
            'video_id': video_id,
            'title': title,
            'description': description,
            'tags': tags,
            'complete_path': complete_path,
        })

    return targets


def get_current_metadata(youtube, video_id):
    """YouTube에서 현재 영상 메타데이터 조회"""
    response = youtube.videos().list(
        part='snippet',
        id=video_id,
    ).execute()

    items = response.get('items', [])
    if not items:
        return None

    snippet = items[0]['snippet']
    return {
        'title': snippet.get('title', ''),
        'description': snippet.get('description', ''),
        'tags': snippet.get('tags', []),
        'categoryId': snippet.get('categoryId', '10'),
    }


def update_video(youtube, target):
    """YouTube 영상 메타데이터 업데이트"""
    body = {
        'id': target['video_id'],
        'snippet': {
            'title': target['title'],
            'description': target['description'],
            'tags': target['tags'],
            'categoryId': '10',
        },
    }

    youtube.videos().update(
        part='snippet',
        body=body,
    ).execute()

    safe_print(f"  업데이트 완료: https://youtu.be/{target['video_id']}")


def update_complete_file(target):
    """완료 파일의 제목 업데이트"""
    with open(target['complete_path'], 'r', encoding='utf-8') as f:
        content = f.read()

    # 첫 줄(제목) 업데이트
    lines = content.split('\n')
    lines[0] = f"# {target['title']}"

    with open(target['complete_path'], 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def main():
    filter_name = sys.argv[1] if len(sys.argv) > 1 else None

    targets = find_update_targets(filter_name)
    if not targets:
        safe_print("업데이트 대상이 없습니다.")
        return

    safe_print(f"\n업데이트 대상:")
    for t in targets:
        safe_print(f"  {t['name']} (https://youtu.be/{t['video_id']})")
        safe_print(f"    제목: {t['title']}")

    safe_print("")
    youtube = get_authenticated_service()

    results = []
    for t in targets:
        safe_print(f"\n처리: {t['name']}")

        # 현재 YouTube 메타데이터 조회
        current = get_current_metadata(youtube, t['video_id'])
        if not current:
            safe_print(f"  영상을 찾을 수 없습니다: {t['video_id']}")
            results.append((t['name'], "실패: 영상 없음"))
            continue

        # 변경 사항 확인
        changes = []
        if current['title'] != t['title']:
            changes.append(f"제목: {current['title']} → {t['title']}")
        if current['description'] != t['description']:
            changes.append("설명: 변경됨")
        if sorted(current.get('tags', [])) != sorted(t['tags']):
            changes.append("태그: 변경됨")

        if not changes:
            safe_print(f"  변경 사항 없음 (건너뜁니다)")
            results.append((t['name'], "변경 없음"))
            continue

        for c in changes:
            safe_print(f"  {c}")

        try:
            update_video(youtube, t)
            results.append((t['name'], "성공"))
        except Exception as e:
            safe_print(f"  오류: {e}")
            results.append((t['name'], f"실패: {e}"))

    safe_print("\n\n=== 업데이트 결과 ===")
    for name, status in results:
        safe_print(f"  {name}: {status}")


if __name__ == "__main__":
    main()
