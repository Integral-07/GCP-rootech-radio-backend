import os
import re
from datetime import datetime, timezone, timedelta

import functions_framework
import google.auth.transport.requests
import google.oauth2.id_token
import requests

JST = timezone(timedelta(hours=9))

RSS_SERVICE_URL = os.environ.get("RSS_SERVICE_URL", "")
SUMMARIZE_SERVICE_URL = os.environ.get("SUMMARIZE_SERVICE_URL", "")
TTS_SERVICE_URL = os.environ.get("TTS_SERVICE_URL", "")
VIDEO_SERVICE_URL = os.environ.get("VIDEO_SERVICE_URL", "")
UPLOAD_SERVICE_URL = os.environ.get("UPLOAD_SERVICE_URL", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

CALL_TIMEOUT_SECONDS = 1800


def notify_discord(message: str, level: str = "INFO") -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    timestamp = datetime.now(JST).strftime("%H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}"
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": f"```\n{line}\n```"},
            timeout=10,
        )
    except requests.RequestException:
        pass


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
    # 水平線・区切り線(---, ***, ___ が3個以上、行全体を占める行)を除去。
    # 除去せずTTSに渡すと、記号の連続がノイズ的な音として発音されることがある
    text = re.sub(r"^\s*([-*_])\1{2,}\s*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^#{1,6}\s*.*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\-\*・]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`{1,3}(.+?)`{1,3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"[#*_`~>]", "", text)
    return text


@functions_framework.http
def run_pipeline(request):
    request_json = request.get_json(silent=True) or {}

    topic = request_json.get("topic", "")
    feed_urls = request_json.get("feed_urls", [])
    day_of_week = request_json.get("day_of_week", "")

    date_str = datetime.now(JST).strftime("%Y%m%d")
    base_filename = f"{date_str}_{topic or 'episode'}".replace(" ", "_")
    script_filename = f"{base_filename}.txt"
    audio_filename = f"{base_filename}.mp3"
    video_filename = f"{base_filename}.mp4"

    steps = {}

    notify_discord(
        f"pipeline started topic=\"{topic or 'unspecified'}\" day={day_of_week or 'unspecified'}",
        "INFO",
    )

    if not RSS_SERVICE_URL:
        notify_discord("RSS_SERVICE_URL is not set, aborting", "ERROR")
        return {"error": "RSS_SERVICE_URL が設定されていません"}, 500

    notify_discord("step=rss status=start", "INFO")
    rss_result = call_service(RSS_SERVICE_URL, {"topic": topic, "feed_urls": feed_urls})
    steps["rss"] = {"count": rss_result.get("count"), "status": "success" if "articles" in rss_result else "error"}
    if "articles" not in rss_result:
        steps["rss"]["detail"] = rss_result
        notify_discord(f"step=rss status=failed detail={str(rss_result)[:1200]}", "ERROR")
        return {"status": "error", "failed_step": "rss", "steps": steps}, 502

    articles = rss_result["articles"]
    notify_discord(f"step=rss status=ok count={rss_result.get('count')}", "INFO")

    if not SUMMARIZE_SERVICE_URL:
        notify_discord("SUMMARIZE_SERVICE_URL is not set, aborting", "ERROR")
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
        notify_discord(f"step=summarize status=failed detail={str(summarize_result)[:1200]}", "ERROR")
        return {"status": "error", "failed_step": "summarize", "steps": steps}, 502

    script_text = summarize_result.get("script_text", "")
    script_text_tts = summarize_result.get("script_text_tts", script_text)
    if summarize_result.get("warning"):
        notify_discord(f"step=summarize warning={summarize_result.get('warning')}", "WARN")
    notify_discord(
        f"step=summarize status=ok chars={summarize_result.get('script_length')}",
        "INFO",
    )

    result = {"status": "success", "steps": steps, "topic": topic}

    if not TTS_SERVICE_URL:
        result["steps"]["tts"] = {"status": "skipped", "reason": "TTS_SERVICE_URL未設定"}
        notify_discord("step=tts status=skipped reason=TTS_SERVICE_URL_unset", "INFO")
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
        notify_discord(f"step=tts status=failed detail={str(tts_result)[:1200]}", "ERROR")
        return result

    duration_min = round((tts_result.get("duration_seconds") or 0) / 60, 1)
    notify_discord(
        f"step=tts status=ok chunks={tts_result.get('chunks_synthesized')} duration_min={duration_min}",
        "INFO",
    )

    if not VIDEO_SERVICE_URL:
        steps["video"] = {"status": "skipped", "reason": "VIDEO_SERVICE_URL未設定"}
        notify_discord("step=video status=skipped reason=VIDEO_SERVICE_URL_unset", "INFO")
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
        notify_discord(f"step=video status=failed detail={str(video_result)[:1200]}", "ERROR")
        return result

    notify_discord(f"step=video status=ok bgm={video_result.get('bgm_used')}", "INFO")

    sources = [
        {"title": a.get("title", ""), "url": a.get("url", "")}
        for a in articles
        if a.get("url")
    ]

    if not UPLOAD_SERVICE_URL:
        steps["upload"] = {"status": "skipped", "reason": "UPLOAD_SERVICE_URL未設定"}
        notify_discord("step=upload status=skipped reason=UPLOAD_SERVICE_URL_unset", "INFO")
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
        notify_discord(f"step=upload status=failed detail={str(upload_result)[:1200]}", "ERROR")
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
