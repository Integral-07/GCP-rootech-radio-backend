import os
from datetime import datetime, timezone

import functions_framework
from google.cloud import firestore

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

db = firestore.Client()


def _cors_headers():
    return {
        "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _episode_to_dict(doc) -> dict:
    data = doc.to_dict()
    published_at = data.get("published_at")
    return {
        "id": doc.id,
        "date": data.get("date", ""),
        "topic": data.get("topic", ""),
        "youtube_video_id": data.get("youtube_video_id", ""),
        "youtube_url": data.get("youtube_url", ""),
        "script": data.get("script", ""),
        "day_of_week": data.get("day_of_week", ""),
        "sources": data.get("sources", []),
        "published_at": published_at.isoformat() if published_at else None,
    }


@functions_framework.http
def get_episodes(request):
    if request.method == "OPTIONS":
        return ("", 204, _cors_headers())

    headers = _cors_headers()
    path = request.path.strip("/")

    if path == "today" or path == "":
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        docs = db.collection("episodes").where("date", "==", today_str).stream()
        episodes = [_episode_to_dict(doc) for doc in docs]
        if not episodes:
            return ({"error": "本日のエピソードはまだありません", "episodes": []}, 404, headers)
        return ({"episodes": episodes, "count": len(episodes)}, 200, headers)

    if path == "episodes":
        limit = int(request.args.get("limit", 20))
        docs = (
            db.collection("episodes")
            .order_by("published_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        episodes = [_episode_to_dict(doc) for doc in docs]
        return ({"episodes": episodes, "count": len(episodes)}, 200, headers)

    if path.startswith("episodes/"):
        date_str = path.split("/", 1)[1]
        docs = db.collection("episodes").where("date", "==", date_str).stream()
        episodes = [_episode_to_dict(doc) for doc in docs]
        if not episodes:
            return ({"error": f"指定日のエピソードが見つかりません: {date_str}", "episodes": []}, 404, headers)
        return ({"episodes": episodes, "count": len(episodes)}, 200, headers)

    return ({"error": "不明なパスです"}, 404, headers)
