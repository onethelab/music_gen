"""and-universe 사용자의 댓글을 찾는 스크립트"""
import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "token.json")
CLIENT_SECRET_PATH = os.path.join(SCRIPT_DIR, "client_secret.json")


def get_youtube_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def find_comments_by_user(youtube, target_user="and-universe"):
    # 내 채널의 영상 목록 가져오기
    channels = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_id = channels["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # 특정 영상 제목으로 검색
    target_titles = ["회로의 맥동", "픽셀 첼로"]

    # 전체 영상 가져오기 (페이지네이션)
    videos = []
    next_page = None
    while True:
        request = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_id, maxResults=50,
            pageToken=next_page
        )
        response = request.execute()
        for item in response["items"]:
            title = item["snippet"]["title"]
            vid = item["snippet"]["resourceId"]["videoId"]
            # 타겟 영상만 필터
            for t in target_titles:
                if t in title:
                    videos.append({"videoId": vid, "title": title})
                    print(f"  영상 발견: {title} ({vid})")
        next_page = response.get("nextPageToken")
        if not next_page:
            break

    print(f"\n총 {len(videos)}개 대상 영상에서 '{target_user}' 댓글 검색 중...\n")

    found = []
    for v in videos:
        try:
            comments_request = youtube.commentThreads().list(
                part="snippet,replies",
                videoId=v["videoId"],
                maxResults=100,
                textFormat="plainText",
            )
            comments_response = comments_request.execute()
            total = comments_response.get("pageInfo", {}).get("totalResults", 0)
            items = comments_response.get("items", [])
            print(f"\n[{v['title']}] 댓글 수: {len(items)} (total: {total})")

            for thread in comments_response.get("items", []):
                top = thread["snippet"]["topLevelComment"]["snippet"]
                author = top["authorDisplayName"]

                # 모든 댓글 출력
                print(f"  [{v['title'][:30]}] {author}: {top['textDisplay'][:80]}")

                if target_user.lower() in author.lower():
                    comment_id = thread["snippet"]["topLevelComment"]["id"]
                    print(f"=== 발견! ===")
                    print(f"  영상: {v['title']}")
                    print(f"  영상ID: {v['videoId']}")
                    print(f"  댓글 작성자: {author}")
                    print(f"  댓글 내용: {top['textDisplay']}")
                    print(f"  댓글 ID: {comment_id}")
                    print(f"  작성일: {top['publishedAt']}")

                    # 기존 답글 확인
                    if thread.get("replies"):
                        print(f"  기존 답글 수: {len(thread['replies']['comments'])}")
                        for reply in thread["replies"]["comments"]:
                            r = reply["snippet"]
                            print(f"    - {r['authorDisplayName']}: {r['textDisplay'][:80]}")
                    else:
                        print(f"  기존 답글: 없음")
                    print()

                    found.append({
                        "videoId": v["videoId"],
                        "videoTitle": v["title"],
                        "commentId": comment_id,
                        "author": author,
                        "text": top["textDisplay"],
                    })
        except Exception as e:
            print(f"  [에러] {v['title'][:30]}: {e}")

    return found


if __name__ == "__main__":
    youtube = get_youtube_service()
    results = find_comments_by_user(youtube, "and-universe")
    print(f"\n총 {len(results)}개의 and-universe 댓글을 찾았습니다.")
