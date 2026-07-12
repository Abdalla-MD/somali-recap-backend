"""
Somali Recap AI Studio — Phase 2A backend.

Endpoints:
  GET  /health
  POST /transcribe        (multipart video file -> {language, segments})
  POST /detect-scenes     (multipart video file -> {scenes})
  POST /generate-script    (JSON {segments} -> {segments} with somali_text)
  POST /synthesize-voice   (JSON {text, voice, speed, pitch} -> mp3 file)

CHANGED for Phase 2A: /transcribe now returns timestamped segments
(not flat text), and /generate-script takes those segments and
returns the same list with somali_text + version/status fields added
— this structured list is the Sync Engine's "source of truth" going
forward. /detect-scenes (new) finds shot-cut boundaries with
PySceneDetect — the client merges scene_id into segments by matching
each segment's start time against the scene ranges (pure timestamp
comparison, no AI needed, so it's done client-side for now).

Run:
  pip install -r requirements.txt
  cp .env.example .env   # then fill in GEMINI_API_KEY and GROQ_API_KEY
  uvicorn main:app --reload --host 0.0.0.0 --port 8000

See README.md for how Flutter should reach this.
"""
import os
import shutil
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from services.transcription_service import transcribe
from services.gemini_service import generate_somali_segments
from services.tts_service import synthesize
from services.scene_detection_service import detect_scenes

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


@app.post("/detect-scenes")
async def detect_scenes_endpoint(file: UploadFile = File(...)):
    temp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{file.filename}")
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        scenes = detect_scenes(temp_path)
        return {"scenes": scenes}
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
