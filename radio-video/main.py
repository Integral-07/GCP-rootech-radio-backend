import os
import random
import subprocess
import tempfile

import functions_framework
from google.cloud import storage

BUCKET_NAME = os.environ.get("BUCKET_NAME", "your-project-id-radio-audio")
BGM_PREFIX = "bgm/"
BACKGROUND_IMAGE_PATH = "assets/background.jpg"

BGM_VOLUME = 0.07
BGM_TREBLE_GAIN = -16
BGM_TREBLE_FREQ = 4000

WAVE_COLOR = "White"
WAVE_OPACITY = 0.3
WAVE_AMPLITUDE = 2.5

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720


def pick_random_bgm(bucket) -> str:
    blobs = list(bucket.list_blobs(prefix=BGM_PREFIX))
    bgm_blobs = [b for b in blobs if b.name.lower().endswith((".mp3", ".wav", ".m4a"))]

    if not bgm_blobs:
        return None

    chosen = random.choice(bgm_blobs)
    local_path = os.path.join(tempfile.gettempdir(), os.path.basename(chosen.name))
    chosen.download_to_filename(local_path)
    return local_path


def download_background_image(bucket) -> str:
    blob = bucket.blob(BACKGROUND_IMAGE_PATH)
    local_path = os.path.join(tempfile.gettempdir(), "background.jpg")
    blob.download_to_filename(local_path)
    return local_path


@functions_framework.http
def create_video(request):
    request_json = request.get_json(silent=True)

    if not request_json or "audio_filename" not in request_json:
        return {"error": "リクエストボディに 'audio_filename' フィールドが必要です"}, 400

    audio_filename = request_json["audio_filename"]
    video_filename = request_json.get(
        "video_filename", audio_filename.rsplit(".", 1)[0] + ".mp4"
    )

    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)

    narration_blob = bucket.blob(audio_filename)
    if not narration_blob.exists():
        return {"error": f"音声ファイルが見つかりません: {audio_filename}"}, 404

    narration_path = os.path.join(tempfile.gettempdir(), "narration.mp3")
    narration_blob.download_to_filename(narration_path)

    background_path = download_background_image(bucket)
    bgm_path = pick_random_bgm(bucket)

    output_path = os.path.join(tempfile.gettempdir(), "output.mp4")

    wave_height = int(VIDEO_HEIGHT * 0.6)
    wave_y = int(VIDEO_HEIGHT * 0.2)

    if bgm_path:
        filter_complex = (
            f"[2:a]aloop=loop=-1:size=2e9,"
            f"treble=g={BGM_TREBLE_GAIN}:f={BGM_TREBLE_FREQ},"
            f"volume={BGM_VOLUME}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0[mixed];"
            f"[mixed]asplit=2[aout][wave_src];"
            f"[1:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[bg];"
            f"[wave_src]volume={WAVE_AMPLITUDE},"
            f"showwaves=s={VIDEO_WIDTH}x{wave_height}:"
            f"mode=cline:colors={WAVE_COLOR}:rate=25,"
            f"format=rgba,colorchannelmixer=aa={WAVE_OPACITY}[wave];"
            f"[bg][wave]overlay=x=0:y={wave_y}[vout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", narration_path,
            "-loop", "1", "-i", background_path,
            "-i", bgm_path,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-shortest",
            output_path,
        ]
    else:
        filter_complex = (
            f"[0:a]asplit=2[aout][wave_src];"
            f"[1:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[bg];"
            f"[wave_src]volume={WAVE_AMPLITUDE},"
            f"showwaves=s={VIDEO_WIDTH}x{wave_height}:"
            f"mode=cline:colors={WAVE_COLOR}:rate=25,"
            f"format=rgba,colorchannelmixer=aa={WAVE_OPACITY}[wave];"
            f"[bg][wave]overlay=x=0:y={wave_y}[vout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", narration_path,
            "-loop", "1", "-i", background_path,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-shortest",
            output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {
            "error": "ffmpeg処理に失敗しました",
            "stderr": result.stderr[-2000:],
        }, 500

    video_blob = bucket.blob(video_filename)
    video_blob.upload_from_filename(output_path, content_type="video/mp4")

    return {
        "status": "success",
        "bucket": BUCKET_NAME,
        "video_filename": video_filename,
        "gcs_path": f"gs://{BUCKET_NAME}/{video_filename}",
        "bgm_used": os.path.basename(bgm_path) if bgm_path else None,
    }
