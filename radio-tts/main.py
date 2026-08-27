import io
import os
import re

import functions_framework
from google.cloud import storage, texttospeech
from pydub import AudioSegment
from pydub.silence import detect_leading_silence

BUCKET_NAME = os.environ.get("BUCKET_NAME", "your-project-id-radio-audio")
VOICE_NAME = os.environ.get("VOICE_NAME", "ja-JP-Neural2-B")

MAX_CHARS_PER_CHUNK = 1200
PAUSE_MS = 350


def split_text_into_chunks(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list:
    sentences = re.split(r"(?<=[。!?\n])", text)

    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)

    return chunks


def trim_silence(segment: AudioSegment, silence_thresh: int = -45) -> AudioSegment:
    start_trim = detect_leading_silence(segment, silence_threshold=silence_thresh)
    end_trim = detect_leading_silence(
        segment.reverse(), silence_threshold=silence_thresh
    )
    duration = len(segment)
    return segment[start_trim : duration - end_trim]


@functions_framework.http
def radio_tts(request):
    request_json = request.get_json(silent=True)

    if not request_json or "text" not in request_json:
        return {"error": "リクエストボディに 'text' フィールドが必要です"}, 400

    text = request_json["text"]
    filename = request_json.get("filename", "output.mp3")

    chunks = split_text_into_chunks(text)

    tts_client = texttospeech.TextToSpeechClient()
    voice = texttospeech.VoiceSelectionParams(
        language_code="ja-JP",
        name=VOICE_NAME,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=1.0,
    )

    pause = AudioSegment.silent(duration=PAUSE_MS)
    combined = AudioSegment.empty()

    for chunk in chunks:
        synthesis_input = texttospeech.SynthesisInput(text=chunk)
        tts_response = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )

        segment = AudioSegment.from_file(io.BytesIO(tts_response.audio_content), format="mp3")
        segment = trim_silence(segment)

        if len(combined) > 0:
            combined += pause
        combined += segment

    output_buffer = io.BytesIO()
    combined.export(output_buffer, format="mp3", bitrate="128k")
    combined_audio = output_buffer.getvalue()

    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(combined_audio, content_type="audio/mpeg")

    return {
        "status": "success",
        "bucket": BUCKET_NAME,
        "filename": filename,
        "gcs_path": f"gs://{BUCKET_NAME}/{filename}",
        "chunks_synthesized": len(chunks),
        "duration_seconds": round(len(combined) / 1000, 1),
    }
