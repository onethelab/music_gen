"""and-universe 댓글에 답글 게시"""
import os
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


def reply_to_comment(youtube, parent_id, text):
    body = {
        "snippet": {
            "parentId": parent_id,
            "textOriginal": text,
        }
    }
    response = youtube.comments().insert(part="snippet", body=body).execute()
    return response


REPLIES = [
    {
        "commentId": "UgxZdaWAvV7e-jB1tDB4AaABAg",
        "video": "픽셀 첼로 V2",
        "text": "Это ответ, переведённый с помощью ИИ — Спасибо, что послушали музыку и оставили комментарий. Если у вас есть идеи, напишите в комментариях. Я попробую сделать из этого музыку!",
    },
    {
        "commentId": "Ugx6WAHiEV6TxhZWMLl4AaABAg",
        "video": "회로의 맥동 V2",
        "text": "Этот ответ переведён с помощью ИИ — Благодарю за то, что слушаете нашу музыку и пишете отзывы. Если у вас есть предложения, оставьте их в комментариях — я постараюсь воплотить их в музыке!",
    },
]

if __name__ == "__main__":
    youtube = get_youtube_service()

    for r in REPLIES:
        print(f"[{r['video']}] 답글 게시 중...")
        try:
            result = reply_to_comment(youtube, r["commentId"], r["text"])
            print(f"  완료! 답글 ID: {result['id']}")
        except Exception as e:
            print(f"  에러: {e}")
