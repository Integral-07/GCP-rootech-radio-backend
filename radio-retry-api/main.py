import base64
import json
import os

import functions_framework
import google.auth.transport.requests
import google.oauth2.id_token
import requests

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "")
RETRY_SECRET = os.environ.get("RETRY_SECRET", "")

CALL_TIMEOUT_SECONDS = 1800


def get_id_token(target_url: str) -> str:
    auth_req = google.auth.transport.requests.Request()
    return google.oauth2.id_token.fetch_id_token(auth_req, target_url)


@functions_framework.http
def retry(request):
    secret = request.args.get("secret", "")
    if not RETRY_SECRET or secret != RETRY_SECRET:
        return {"error": "不正なリクエストです"}, 403

    encoded = request.args.get("data", "")
    if not encoded:
        return {"error": "'data' パラメータが必要です"}, 400

    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
    except Exception as e:
        return {"error": f"data のデコードに失敗しました: {e}"}, 400

    if not ORCHESTRATOR_URL:
        return {"error": "ORCHESTRATOR_URL が設定されていません"}, 500

    token = get_id_token(ORCHESTRATOR_URL)
    resp = requests.post(
        ORCHESTRATOR_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=CALL_TIMEOUT_SECONDS,
    )

    try:
        return resp.json(), resp.status_code
    except ValueError:
        return {"status": "error", "raw_response": resp.text[:2000]}, resp.status_code
