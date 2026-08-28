import os
import re
import time
import tempfile
from datetime import datetime, timezone, timedelta

import functions_framework
from google.cloud import firestore, storage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

JST = timezone(timedelta(hours=9))

BUCKET_NAME = os.environ.get("BUCKET_NAME", "your-project-id-radio-audio")
YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
SITE_URL = os.environ.get("SITE_URL", "https://rootechradio.web.app")

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

WEEKDAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]

TOPIC_PLAYLIST_MAP = {
    "AI・機械学習": "PLS0ZK7T0VeTM",
    "Web開発・フロントエンド": "PLAV7rqC-tM9I",
    "セキュリティ": "PLL1jCSwgOUiQ",
    "バックエンド・DB": "PLfJAEDe9uXU4",
    "クラウド・インフラ": "PLd1MFMV_iVWw",
    "プログラミング言語・OSSトレンド": "PLNQOWfznuE2g",
    "ハードウェア・ガジェット": "PLKXd7IuAUMAQ",
    "気になる企業ウォッチ": "PLej9Q5cKzQac",
    "週間まとめ": "PLPJSmi3J7HyA",
}


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


def video_in_playlist(youtube, playlist_id: str, video_id: str) -> bool:
    resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=playlist_id,
        maxResults=10,
    ).execute()
    video_ids = [
        item["snippet"]["resourceId"]["videoId"]
        for item in resp.get("items", [])
        if item.get("snippet", {}).get("resourceId", {}).get("videoId")
    ]
    return video_id in video_ids


def add_to_playlist_and_verify(youtube, playlist_id: str, video_id: str) -> bool:
    if video_in_playlist(youtube, playlist_id, video_id):
        return True

    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()

    return video_in_playlist(youtube, playlist_id, video_id)


@functions_framework.http
def upload_video(request):
    request_json = request.get_json(silent=True)

    if not request_json:
        return {"error": "リクエストボディが必要です"}, 400

    if "debug_playlist_video_id" in request_json:
        if not all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN]):
            return {"error": "YouTube認証用の環境変数が設定されていません"}, 500

        video_id = request_json["debug_playlist_video_id"]
        topic = request_json.get("topic", "")
        playlist_id = TOPIC_PLAYLIST_MAP.get(topic)

        if not playlist_id:
            return {"error": f"トピック '{topic}' に対応する再生リストがありません"}, 400

        youtube = get_youtube_client()
        try:
            verified = add_to_playlist_and_verify(youtube, playlist_id, video_id)
            return {"status": "success" if verified else "error", "playlist_id": playlist_id, "verified": verified}
        except Exception as e:
            return {"status": "error", "detail": str(e)}, 500

    if "video_filename" not in request_json:
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
            "privacyStatus": "public",
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

    playlist_id = TOPIC_PLAYLIST_MAP.get(topic)
    playlist_result = {"status": "skipped", "reason": "対応する再生リストなし"}
    if playlist_id:
        max_retries = 5
        verified = False
        last_error = None
        for attempt in range(max_retries):
            if attempt > 0:
                wait_seconds = 10 * (2 ** (attempt - 1))
                time.sleep(wait_seconds)
            try:
                verified = add_to_playlist_and_verify(youtube, playlist_id, video_id)
                if verified:
                    last_error = None
                    break
                last_error = RuntimeError("insert succeeded but video not found in playlist on verification")
                print(f"[radio-upload] playlist verify attempt {attempt + 1} failed: not found after insert")
            except Exception as e:
                last_error = e
                print(f"[radio-upload] playlist add attempt {attempt + 1} failed: {e}")

        if verified:
            playlist_result = {"status": "success", "playlist_id": playlist_id}
        else:
            playlist_result = {"status": "error", "detail": str(last_error)[:500]}

    script = request_json.get("script", "")
    day_of_week = request_json.get("day_of_week") or WEEKDAY_NAMES[datetime.now(JST).weekday()]
    today_str = datetime.now(JST).strftime("%Y-%m-%d")

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
        "playlist_result": playlist_result,
    }
