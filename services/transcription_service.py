
"""
Transcription via Groq's hosted Whisper Large v3 API — replaces the
earlier approach of running faster-whisper directly on our own
server. Why this changed:

  1. Groq is free (generous limits: 20 req/min, 2000 req/day) and
     MUCH faster (~228x real-time vs CPU-bound local inference).
  2. It removes a heavy dependency (faster-whisper + model download)
     from our backend, so hosting needs less RAM/CPU.

Groq's API accepts video files directly (mp4 is supported), but has
a 25MB file size limit — real phone videos often exceed that. So we
still extract just the audio track locally first (ffmpeg, already in
the Docker image), which is small (a few MB even for a few minutes)
and well under the limit, then send THAT to Groq.
"""
import os
import subprocess
import uuid

import requests

_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_MODEL = "whisper-large-v3"

TEMP_DIR = "uploads"
os.makedirs(TEMP_DIR, exist_ok=True)


def _extract_audio(video_path: str) -> str:
    """
    Extracts a small mono 16kHz mp3 audio track from the video using
    ffmpeg (matches what Whisper models want internally anyway, so
    pre-downsampling here just makes the upload to Groq smaller/faster).
    """
    audio_path = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}.mp3")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn",                  # no video
            "-acodec", "libmp3lame",
            "-ar", "16000",         # 16kHz sample rate
            "-ac", "1",             # mono
            "-b:a", "64k",
            audio_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr}")
    return audio_path


def transcribe(video_path: str) -> dict:
    """
    Returns: {"language": "en", "text": "full transcript ..."}
    Raises RuntimeError on any failure (missing key, ffmpeg error,
    Groq API error).
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set — check your .env file.")

    audio_path = _extract_audio(video_path)
    try:
        with open(audio_path, "rb") as audio_file:
            response = requests.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (os.path.basename(audio_path), audio_file, "audio/mpeg")},
                data={"model": _MODEL, "response_format": "json"},
                timeout=120,
            )

        if response.status_code != 200:
            raise RuntimeError(f"Groq API error {response.status_code}: {response.text}")

        data = response.json()
        return {
            "language": data.get("language", "unknown"),
            "text": data.get("text", "").strip(),
        }
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)
