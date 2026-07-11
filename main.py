"""
Somali Recap AI Studio — Phase 1 test backend.

Endpoints:
  GET  /health
  POST /transcribe        (multipart video file -> transcript JSON)
  POST /generate-script    (JSON {transcript} -> JSON {script})
  POST /synthesize-voice   (JSON {text, voice, speed, pitch} -> mp3 file)

Run:
  pip install -r requirements.txt
  cp .env.example .env   # then fill in GEMINI_API_KEY
  uvicorn main:app --reload --host 0.0.0.0 --port 8000

See README.md for how Flutter should reach this (10.0.2.2 for the
Android emulator, or your machine's LAN IP for a physical device).
"""
import os
import shutil
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from services.transcription_service import transcribe
from services.gemini_service import generate_somali_script
from services.tts_service import synthesize

load_dotenv()

app = FastAPI(title="Somali Recap AI Studio - Backend (Phase 1 test server)")

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


class ScriptRequest(BaseModel):
    transcript: str


@app.post("/generate-script")
async def generate_script_endpoint(payload: ScriptRequest):
    if not payload.transcript.strip():
        return JSONResponse(status_code=400, content={"error": "transcript is required"})
    try:
        script = generate_somali_script(payload.transcript)
        return {"script": script}
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
