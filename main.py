"""
Somali Recap AI Studio — Phase 2A backend (CPU-optimized for
Render.com's free tier).

Endpoints:
  GET  /health
  POST /transcribe        (multipart video file -> {language, segments})
  POST /generate-script    (JSON {segments} -> {segments} with somali_text)
  POST /synthesize-voice   (JSON {text, voice, speed, pitch} -> mp3 file)
  POST /render             (multipart video + segments JSON -> final MP4)

CHANGED for Phase 2A: /transcribe now returns timestamped segments
(not flat text), and /generate-script takes those segments and
returns the same list with somali_text + version/status fields added
— this structured list is the Sync Engine's "source of truth".

REMOVED (deliberately): Scene Detection (PySceneDetect) and the
/detect-scenes endpoint. It scanned every frame of the video (3-5+
minutes on Render's 0.1 CPU free tier for a 10-min 1080p video) but
the Decision Engine never actually used scene_id in its logic — pure
CPU cost with no payoff. scene_detection_service.py is kept in the
repo but unused, in case a real need for it comes up later.

Run:
  pip install -r requirements.txt
  cp .env.example .env   # then fill in GEMINI_API_KEY and GROQ_API_KEY
  uvicorn main:app --reload --host 0.0.0.0 --port 8000

See README.md for how Flutter should reach this.
"""
import json
import os
import shutil
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from services.transcription_service import transcribe
from services.gemini_service import generate_somali_segments
from services.tts_service import synthesize
from services.simple_render_service import render_simple_video

load_dotenv()

app = FastAPI(title="Somali Recap AI Studio - Backend (Phase 2A)")

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe_endpoint(file: UploadFile = File(...)):
    temp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{file.filename}")
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = transcribe(temp_path)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


class WhisperSegment(BaseModel):
    start: float
    end: float
    text: str


class ScriptRequest(BaseModel):
    segments: list[WhisperSegment]


@app.post("/generate-script")
async def generate_script_endpoint(payload: ScriptRequest):
    if not payload.segments:
        return JSONResponse(status_code=400, content={"error": "segments is required"})
    try:
        segments_dict = [s.model_dump() for s in payload.segments]
        result = generate_somali_segments(segments_dict)
        return {"segments": result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


class VoiceRequest(BaseModel):
    text: str
    voice: str = "muuse"
    speed: float = 1.0
    pitch: float = 0.0


@app.post("/synthesize-voice")
async def synthesize_voice_endpoint(payload: VoiceRequest):
    if not payload.text.strip():
        return JSONResponse(status_code=400, content={"error": "text is required"})
    try:
        filepath = await synthesize(payload.text, payload.voice, payload.speed, payload.pitch)
        return FileResponse(filepath, media_type="audio/mpeg", filename=os.path.basename(filepath))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/render")
async def render_endpoint(
    file: UploadFile = File(...),
    segments: str = Form(...),
    voice: str = Form("muuse"),
    speed: float = Form(1.0),
    pitch: float = Form(0.0),
):
    """
    SIMPLE MODE (current, per Abdalla): synthesizes each segment's
    audio, concatenates it into one narration track, and overlays it
    onto the ORIGINAL video with no re-encoding (-c:v copy). No trim,
    no freeze/zoom, no motion/semantic/decision analysis — those are
    parked (see simple_render_service.py's docstring) for when a more
    capable VPS is available. This is deliberately the lightest
    possible version that still produces a real dubbed video, sized
    to actually run on Render's free tier.
    """
    temp_video_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{file.filename}")
    with open(temp_video_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        segment_list = json.loads(segments)
        if not segment_list:
            return JSONResponse(status_code=400, content={"error": "segments is empty"})

        # Synthesize each segment's real audio (edge-tts).
        segment_audio_paths = {}
        for seg in segment_list:
            audio_path = await synthesize(seg["somali_text"], voice, speed, pitch)
            segment_audio_paths[seg["segment_id"]] = audio_path

        # Concatenate audio + overlay onto the original video, no
        # video re-encoding at all.
        final_path = render_simple_video(temp_video_path, segment_list, segment_audio_paths)

        return FileResponse(
            final_path,
            media_type="video/mp4",
            filename="somali_recap_final.mp4",
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        if os.path.exists(temp_video_path):
            os.remove(temp_video_path)
