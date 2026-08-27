import os
import re
from datetime import datetime, timezone, timedelta

import functions_framework
from google.cloud import firestore

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

MAX_MESSAGE_LENGTH = 2000
MAX_NAME_LENGTH = 100

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 3

db = firestore.Client()


def _cors_headers():
    return {
        "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _get_client_ip(request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _is_rate_limited(ip_address: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
    recent = (
        db.collection("feedback")
        .where("ip_address", "==", ip_address)
        .where("submitted_at", ">=", cutoff)
        .stream()
    )
    return len(list(recent)) >= RATE_LIMIT_MAX_REQUESTS


@functions_framework.http
def submit_feedback(request):
    if request.method == "OPTIONS":
        return ("", 204, _cors_headers())

    headers = _cors_headers()

    if request.method != "POST":
        return ({"error": "POSTメソッドのみ対応しています"}, 405, headers)

    request_json = request.get_json(silent=True)
    if not request_json or not request_json.get("message", "").strip():
        return ({"error": "'message' フィールドが必要です"}, 400, headers)

    message = request_json["message"].strip()[:MAX_MESSAGE_LENGTH]
    name = request_json.get("name", "").strip()[:MAX_NAME_LENGTH]
    episode_id = request_json.get("episode_id", "").strip()

    ip_address = _get_client_ip(request)

    if _is_rate_limited(ip_address):
        return (
            {"error": "投稿が多すぎます。しばらく待ってから再度お試しください"},
            429,
            headers,
        )

    doc_ref = db.collection("feedback").document()
    doc_ref.set({
        "message": message,
        "name": name,
        "episode_id": episode_id,
        "ip_address": ip_address,
        "submitted_at": datetime.now(timezone.utc),
        "status": "new",
    })

    return ({"status": "success", "id": doc_ref.id}, 200, headers)
