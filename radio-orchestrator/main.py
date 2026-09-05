import base64
import json
import os
import re
from datetime import datetime, timezone, timedelta

import functions_framework
import google.auth.transport.requests
import google.oauth2.id_token
import requests
from google.cloud import storage

JST = timezone(timedelta(hours=9))

RSS_SERVICE_URL = os.environ.get("RSS_SERVICE_URL", "")
SUMMARIZE_SERVICE_URL = os.environ.get("SUMMARIZE_SERVICE_URL", "")
TTS_SERVICE_URL = os.environ.get("TTS_SERVICE_URL", "")
VIDEO_SERVICE_URL = os.environ.get("VIDEO_SERVICE_URL", "")
UPLOAD_SERVICE_URL = os.environ.get("UPLOAD_SERVICE_URL", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
RETRY_API_URL = os.environ.get("RETRY_API_URL", "")
RETRY_SECRET = os.environ.get("RETRY_SECRET", "")

CALL_TIMEOUT_SECONDS = 1800

STEP_ORDER = ["rss", "summarize", "tts", "video", "upload"]


def build_retry_url(
    start_step: str,
    topic: str,
    day_of_week: str,
    feed_urls: list,
    script_filename: str,
    audio_filename: str,
    video_filename: str,
    sources: list,
) -> str:
    if not RETRY_API_URL or not RETRY_SECRET:
        return ""

    payload = {
        "start_step": start_step,
        "topic": topic,
        "day_of_week": day_of_week,
        "feed_urls": feed_urls,
        "script_filename": script_filename,
        "audio_filename": audio_filename,
        "video_filename": video_filename,
        "sources": sources,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    return f"{RETRY_API_URL}?secret={RETRY_SECRET}&data={encoded}"


def notify_discord(message: str, level: str = "INFO", retry_url: str = "") -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    timestamp = datetime.now(JST).strftime("%H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}"
    content = f"```\n{line}\n```"
    if retry_url:
        content += f"\ncontinue pipeline: <{retry_url}>"
    payload = {"content": content}
    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=10,
        )
        if resp.status_code >= 400:
            print(f"[notify_discord] Discord API error: {resp.status_code} {resp.text}")
    except requests.RequestException as e:
        print(f"[notify_discord] request failed: {e}")


def get_id_token(target_url: str) -> str:
    auth_req = google.auth.transport.requests.Request()
    return google.oauth2.id_token.fetch_id_token(auth_req, target_url)


def call_service(url: str, payload: dict) -> dict:
    token = get_id_token(url)
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=CALL_TIMEOUT_SECONDS,
    )
    try:
        body = resp.json()
    except ValueError:
        return {
            "status": "error",
            "http_status_code": resp.status_code,
            "raw_response": resp.text[:2000],
        }
    if not isinstance(body, dict):
        return {"status": "error", "raw_response": str(body)[:2000]}
    body.setdefault("http_status_code", resp.status_code)
    return body


def clean_for_tts(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    text = re.sub(r"^\s*([-*_])\1{2,}\s*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^#{1,6}\s*.*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\-\*・]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`{1,3}(.+?)`{1,3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"[#*_`~>]", "", text)
    return text


def fetch_script_from_gcs(filename: str) -> str:
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    return blob.download_as_text()


@functions_framework.http
def run_pipeline(request):
    request_json = request.get_json(silent=True) or {}

    topic = request_json.get("topic", "")
    feed_urls = request_json.get("feed_urls", [])
    day_of_week = request_json.get("day_of_week", "")
    start_step = request_json.get("start_step", "rss")

    if start_step not in STEP_ORDER:
        return {"error": f"start_step が不正です: {start_step}"}, 400

    date_str = datetime.now(JST).strftime("%Y%m%d")
    base_filename = f"{date_str}_{topic or 'episode'}".replace(" ", "_")
    script_filename = request_json.get("script_filename") or f"{base_filename}.txt"
    audio_filename = request_json.get("audio_filename") or f"{base_filename}.mp3"
    video_filename = request_json.get("video_filename") or f"{base_filename}.mp4"

    steps = {}
    articles = []
    script_text = ""
    script_text_tts = ""
    sources = request_json.get("sources", [])

    notify_discord(
        f"pipeline started topic=\"{topic or 'unspecified'}\" day={day_of_week or 'unspecified'} "
        f"start_step={start_step}",
        "INFO",
    )

    step_index = STEP_ORDER.index(start_step)

    if step_index <= STEP_ORDER.index("rss"):
        if not RSS_SERVICE_URL:
            retry_url = build_retry_url("rss", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                "RSS_SERVICE_URL is not set, aborting",
                "ERROR",
                retry_url=retry_url,
            )
            return {"error": "RSS_SERVICE_URL が設定されていません"}, 500

        notify_discord("step=rss status=start", "INFO")
        rss_result = call_service(RSS_SERVICE_URL, {"topic": topic, "feed_urls": feed_urls})
        steps["rss"] = {"count": rss_result.get("count"), "status": "success" if "articles" in rss_result else "error"}
        if "articles" not in rss_result:
            steps["rss"]["detail"] = rss_result
            retry_url = build_retry_url("rss", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                f"step=rss status=failed detail={str(rss_result)[:1200]}",
                "ERROR",
                retry_url=retry_url,
            )
            return {"status": "error", "failed_step": "rss", "steps": steps}, 502

        articles = rss_result["articles"]
        notify_discord(f"step=rss status=ok count={rss_result.get('count')}", "INFO")
    else:
        steps["rss"] = {"status": "skipped_by_retry"}

    if articles:
        sources = [
            {"title": a.get("title", ""), "url": a.get("url", "")}
            for a in articles
            if a.get("url")
        ]

    if step_index <= STEP_ORDER.index("summarize"):
        if not SUMMARIZE_SERVICE_URL:
            retry_url = build_retry_url(start_step, topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                "SUMMARIZE_SERVICE_URL is not set, aborting",
                "ERROR",
                retry_url=retry_url,
            )
            return {"error": "SUMMARIZE_SERVICE_URL が設定されていません", "steps": steps}, 500

        notify_discord("step=summarize status=start", "INFO")
        summarize_result = call_service(
            SUMMARIZE_SERVICE_URL,
            {"articles": articles, "filename": script_filename, "topic": topic},
        )
        steps["summarize"] = {
            "status": summarize_result.get("status"),
            "script_length": summarize_result.get("script_length"),
            "warning": summarize_result.get("warning"),
        }
        if summarize_result.get("status") != "success":
            steps["summarize"]["detail"] = summarize_result
            retry_url = build_retry_url("rss", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                f"step=summarize status=failed detail={str(summarize_result)[:1200]}",
                "ERROR",
                retry_url=retry_url,
            )
            return {"status": "error", "failed_step": "summarize", "steps": steps}, 502

        script_text = summarize_result.get("script_text", "")
        script_text_tts = summarize_result.get("script_text_tts", script_text)
        if summarize_result.get("warning"):
            notify_discord(f"step=summarize warning={summarize_result.get('warning')}", "WARN")
        notify_discord(
            f"step=summarize status=ok chars={summarize_result.get('script_length')}",
            "INFO",
        )
    else:
        steps["summarize"] = {"status": "skipped_by_retry"}
        try:
            script_text = fetch_script_from_gcs(script_filename)
            script_text_tts = script_text
        except Exception as e:
            retry_url = build_retry_url("rss", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                f"step=summarize(skip) failed to fetch script from GCS: {e}",
                "ERROR",
                retry_url=retry_url,
            )
            return {"status": "error", "failed_step": "summarize", "detail": str(e)}, 500

    result = {"status": "success", "steps": steps, "topic": topic}

    if step_index <= STEP_ORDER.index("tts"):
        if not TTS_SERVICE_URL:
            result["steps"]["tts"] = {"status": "skipped", "reason": "TTS_SERVICE_URL未設定"}
            retry_url = build_retry_url("tts", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                "step=tts status=skipped reason=TTS_SERVICE_URL_unset",
                "INFO",
                retry_url=retry_url,
            )
            return result

        notify_discord("step=tts status=start", "INFO")
        tts_text = clean_for_tts(script_text_tts)
        tts_result = call_service(TTS_SERVICE_URL, {"text": tts_text, "filename": audio_filename})
        steps["tts"] = {
            "status": tts_result.get("status"),
            "chunks_synthesized": tts_result.get("chunks_synthesized"),
            "duration_seconds": tts_result.get("duration_seconds"),
        }
        if tts_result.get("status") != "success":
            steps["tts"]["detail"] = tts_result
            result["status"] = "partial_success"
            retry_url = build_retry_url("tts", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                f"step=tts status=failed detail={str(tts_result)[:1200]}",
                "ERROR",
                retry_url=retry_url,
            )
            return result

        duration_min = round((tts_result.get("duration_seconds") or 0) / 60, 1)
        notify_discord(
            f"step=tts status=ok chunks={tts_result.get('chunks_synthesized')} duration_min={duration_min}",
            "INFO",
        )
    else:
        steps["tts"] = {"status": "skipped_by_retry"}

    if step_index <= STEP_ORDER.index("video"):
        if not VIDEO_SERVICE_URL:
            steps["video"] = {"status": "skipped", "reason": "VIDEO_SERVICE_URL未設定"}
            retry_url = build_retry_url("video", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                "step=video status=skipped reason=VIDEO_SERVICE_URL_unset",
                "INFO",
                retry_url=retry_url,
            )
            return result

        notify_discord("step=video status=start", "INFO")
        video_result = call_service(
            VIDEO_SERVICE_URL,
            {"audio_filename": audio_filename, "video_filename": video_filename},
        )
        steps["video"] = {
            "status": video_result.get("status"),
            "bgm_used": video_result.get("bgm_used"),
        }
        if video_result.get("status") != "success":
            steps["video"]["detail"] = video_result
            result["status"] = "partial_success"
            retry_url = build_retry_url("video", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
            notify_discord(
                f"step=video status=failed detail={str(video_result)[:1200]}",
                "ERROR",
                retry_url=retry_url,
            )
            return result

        notify_discord(f"step=video status=ok bgm={video_result.get('bgm_used')}", "INFO")
    else:
        steps["video"] = {"status": "skipped_by_retry"}

    if not UPLOAD_SERVICE_URL:
        steps["upload"] = {"status": "skipped", "reason": "UPLOAD_SERVICE_URL未設定"}
        retry_url = build_retry_url("upload", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
        notify_discord(
            "step=upload status=skipped reason=UPLOAD_SERVICE_URL_unset",
            "INFO",
            retry_url=retry_url,
        )
        return result

    date_display = datetime.now(JST).strftime("%Y/%m/%d")

    notify_discord("step=upload status=start", "INFO")
    upload_result = call_service(
        UPLOAD_SERVICE_URL,
        {
            "video_filename": video_filename,
            "title": f"Rootech Radio - {topic} ({date_display})" if topic else f"Rootech Radio ({date_display})",
            "topic": topic,
            "script": script_text,
            "day_of_week": day_of_week,
            "sources": sources,
        },
    )
    steps["upload"] = {
        "status": upload_result.get("status"),
        "youtube_url": upload_result.get("youtube_url"),
        "playlist_result": upload_result.get("playlist_result"),
    }
    if upload_result.get("status") != "success":
        steps["upload"]["detail"] = upload_result
        result["status"] = "partial_success"
        retry_url = build_retry_url("upload", topic, day_of_week, feed_urls, script_filename, audio_filename, video_filename, sources)
        notify_discord(
            f"step=upload status=failed detail={str(upload_result)[:1200]}",
            "ERROR",
            retry_url=retry_url,
        )
        return result

    result["youtube_url"] = upload_result.get("youtube_url")
    playlist_info = upload_result.get("playlist_result") or {}
    notify_discord(
        f"step=upload status=ok url={upload_result.get('youtube_url')} "
        f"playlist_status={playlist_info.get('status')} "
        f"playlist_id={playlist_info.get('playlist_id', '-')}",
        "INFO",
    )
    notify_discord(f"pipeline finished topic=\"{topic}\" status=success", "INFO")
    return result
