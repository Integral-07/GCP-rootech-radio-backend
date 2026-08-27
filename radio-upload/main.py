import os
import re
import tempfile
from datetime import datetime, timezone

import functions_framework
from google.cloud import firestore, storage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BUCKET_NAME = os.environ.get("BUCKET_NAME", "your-project-id-radio-audio")
YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
SITE_URL = os.environ.get("SITE_URL", "https://rootechradio.web.app")

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

WEEKDAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]


def get_youtube_client():
    credentials = Credentials(
        token=None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri=TOKEN_URI,
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=credentials)


@functions_framework.http
def upload_video(request):
    request_json = request.get_json(silent=True)

    if not request_json or "video_filename" not in request_json:
        return {"error": "リクエストボディに 'video_filename' フィールドが必要です"}, 400

    if not all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN]):
        return {"error": "YouTube認証用の環境変数が設定されていません"}, 500

    video_filename = request_json["video_filename"]
    title = request_json.get("title", video_filename)
    description = request_json.get("description", "")
    topic = request_json.get("topic", "")
    sources = request_json.get("sources", [])

    if topic and not description:
        description = (
            f"今回のテーマ: {topic}\n\n"
            f"Rootech Radio(ルーテックラジオ) - 週刊技術ニュースラジオ\n\n"
            f"原稿全文・過去回はこちら: {SITE_URL}"
        )

    if sources:
        sources_lines = [f"・{s['title']}\n  {s['url']}" for s in sources if s.get("url")]
        sources_block = "\n\n参考文献:\n" + "\n".join(sources_lines)
        if len(description) + len(sources_block) > 4900:
            while sources_lines and len(description) + len("\n\n参考文献:\n" + "\n".join(sources_lines)) > 4900:
                sources_lines.pop()
            sources_block = "\n\n参考文献:\n" + "\n".join(sources_lines) if sources_lines else ""
        description += sources_block

    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(video_filename)

    if not blob.exists():
        return {"error": f"動画ファイルが見つかりません: {video_filename}"}, 404

    local_path = os.path.join(tempfile.gettempdir(), video_filename)
    blob.download_to_filename(local_path)

    youtube = get_youtube_client()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "28",
        },
        "status": {
            "privacyStatus": "unlisted",
        },
    }

    media = MediaFileUpload(local_path, chunksize=-1, resumable=True, mimetype="video/mp4")

    request_upload = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request_upload.next_chunk()

    video_id = response.get("id")

    script = request_json.get("script", "")
    day_of_week = request_json.get("day_of_week") or WEEKDAY_NAMES[datetime.now().weekday()]
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    topic_slug = re.sub(r"[^\w一-龠ぁ-んァ-ヶー]+", "-", topic).strip("-") or "episode"
    doc_id = f"{today_str}_{topic_slug}"

    db = firestore.Client()
    db.collection("episodes").document(doc_id).set({
        "topic": topic,
        "youtube_video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "script": script,
        "day_of_week": day_of_week,
        "sources": sources,
        "date": today_str,
        "published_at": datetime.now(timezone.utc),
    })

    return {
        "status": "success",
        "video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "firestore_doc_id": doc_id,
    }
