"""
Transcription via Groq's hosted Whisper Large v3 API.

CHANGED for Phase 2A: now requests verbose_json to get timestamped
segments (not just flat text) — the Sync Engine needs per-sentence
start/end times to build the timeline and align Somali script
segments to the original video.
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
    audio_path = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}.mp3")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-ar", "16000",
            "-ac", "1",
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
    Returns:
        {
          "language": "en",
          "segments": [
            {"start": 0.0, "end": 4.2, "text": "..."},
            {"start": 4.2, "end": 8.9, "text": "..."}
          ]
        }
    Raises RuntimeError on any failure.
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
                data={
                    "model": _MODEL,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
                timeout=120,
            )

        if response.status_code != 200:
            raise RuntimeError(f"Groq API error {response.status_code}: {response.text}")

        data = response.json()
        segments = [
            {
                "start": round(seg["start"], 2),
                "end": round(seg["end"], 2),
                "text": seg["text"].strip(),
            }
            for seg in data.get("segments", [])
        ]
        return {
            "language": data.get("language", "unknown"),
            "segments": segments,
        }
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)
