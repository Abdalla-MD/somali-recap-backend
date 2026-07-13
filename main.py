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
import json
import os
import shutil
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from services.transcription_service import transcribe
from services.gemini_service import generate_somali_segments
from services.tts_service import synthesize, get_audio_duration
from services.scene_detection_service import detect_scenes, merge_scene_ids
from services.motion_analyzer import motion_score
from services.semantic_engine import semantic_scores
from services.decision_engine import decision_engine
from services.ffmpeg_render_service import render_final_video

load_dotenv()

app = FastAPI(title="Somali Recap AI Studio - Backend (Phase 2A)")

# Allow the Flutter web app (and any other client) to call this API
# from a different origin. Without this, browsers block every request
# with an OPTIONS preflight failure (405) before the real POST is sent.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # tighten to your real domain(s) once you have one
    allow_credentials=False,   # must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.post("/render")
async def render_endpoint(
    file: UploadFile = File(...),
    segments: str = Form(...),
    voice: str = Form("muuse"),
    speed: float = Form(1.0),
    pitch: float = Form(0.0),
):
    """
    The full Sync Engine + Freeze Engine + FFmpeg pipeline, tied
    together. `segments` is a JSON string (the structured segment
    list Flutter already built via /transcribe + /detect-scenes +
    /generate-script), each with segment_id/original_text/
    somali_text/start/end/scene_id.

    Steps: synthesize each segment's real audio -> measure real
    voice_duration (ffprobe) -> motion score (OpenCV) -> semantic
    score (Gemini, batched) -> Decision Engine (Rule + AI combined)
    -> FFmpeg render (trim + freeze/zoom/shake + audio) -> final MP4.
    """
    temp_video_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{file.filename}")
    with open(temp_video_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        segment_list = json.loads(segments)
        if not segment_list:
            return JSONResponse(status_code=400, content={"error": "segments is empty"})

        # 1. Scene Detection — moved here from the fast upload/script
        #    flow (PySceneDetect is too slow for Render's free-tier
        #    CPU to run inline with script generation; it belongs
        #    here anyway since scene_id is only actually needed for
        #    the Motion Analyzer / Decision Engine below). A slow or
        #    failed detection here just means no scene_id — it
        #    doesn't block the render (motion/decision still work
        #    off segment start/end directly).
        try:
            scenes = detect_scenes(temp_video_path)
            segment_list = merge_scene_ids(segment_list, scenes)
        except Exception as e:
            print(f"Scene detection failed (non-fatal): {e}")

        # 2. Synthesize each segment's real audio + measure real
        #    voice duration (Voice Duration Analyzer).
        segment_audio_paths = {}
        for seg in segment_list:
            audio_path = await synthesize(seg["somali_text"], voice, speed, pitch)
            segment_audio_paths[seg["segment_id"]] = audio_path
            seg["voice_duration"] = get_audio_duration(audio_path)

        # 3. Motion Analyzer — per segment's own time range.
        for seg in segment_list:
            try:
                seg["motion_score_value"] = motion_score(temp_video_path, seg["start"], seg["end"])
            except Exception:
                # A motion-analysis failure shouldn't block the whole
                # render — fall back to "assume static" (0), which
                # just means the Rule Engine's freeze decision stands
                # unmodified by the motion override.
                seg["motion_score_value"] = 0.0

        # 4. Semantic Engine — batched, one Gemini call for all
        #    segments rather than one call each.
        try:
            sem_scores = semantic_scores([
                {
                    "segment_id": s["segment_id"],
                    "original_text": s["original_text"],
                    "somali_text": s["somali_text"],
                }
                for s in segment_list
            ])
        except Exception:
            # If semantic scoring fails, default everyone to "OK"
            # (100) rather than blocking the render or flagging
            # everything for review incorrectly.
            sem_scores = {}

        # 5. Decision Engine — combines Rule Engine + Motion + Semantic.
        decided_segments = []
        for seg in segment_list:
            decision = decision_engine(
                seg,
                motion_score=seg.get("motion_score_value", 0.0),
                semantic_score=sem_scores.get(seg["segment_id"], 100.0),
            )
            decided_segments.append({**seg, **decision})

        # 6. FFmpeg Render — Cinematic Freeze Engine + final assembly.
        final_path = render_final_video(temp_video_path, decided_segments, segment_audio_paths)

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
