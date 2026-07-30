"""
Simple Render — Phase 2A "lite" mode, built for Render.com's free
tier (0.1 CPU).

No trim, no freeze, no zoom/shake, no motion/semantic analysis, no
per-segment video re-encoding. Just: concatenate the synthesized
Somali audio segments in order, then replace the original video's
audio track with that combined narration — using "-c:v copy" so
FFmpeg never re-encodes the video at all (just repackages the
container). That's what makes this fast enough for a free server:
audio-only work + one cheap remux, instead of 4 video encodes per
segment.

TRADEOFF (accepted deliberately, per Abdalla): no scene-by-scene sync
precision — the video plays at its original pace/timing regardless of
how long the Somali narration runs. The full Sync Engine (Scene
Detection, Motion Analyzer, Semantic Engine, Decision Engine,
Cinematic Freeze Engine) is parked in ffmpeg_render_service.py,
motion_analyzer.py, semantic_engine.py, and decision_engine.py —
still in the repo, just not wired into /render right now. Reactivate
those once a more capable VPS is available (see those files' own
docstrings for what each does).
"""
import os
import subprocess
import uuid

RENDER_DIR = "render_tmp"
os.makedirs(RENDER_DIR, exist_ok=True)


def _run(cmd: list):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg command failed: {' '.join(cmd)}\n{result.stderr}")


def _concat_audio(audio_paths: list, out_path: str):
    """Concatenates mp3 files (already the same format/codec, since
    they all come from the same edge-tts call) into one continuous
    audio track, in order."""
    list_file = os.path.join(RENDER_DIR, f"{uuid.uuid4().hex}_audiolist.txt")
    with open(list_file, "w") as f:
        for p in audio_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    try:
        _run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", out_path,
        ])
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)


def render_simple_video(video_path: str, segments: list, segment_audio_paths: dict) -> str:
    """
    segments: ordered list of segment dicts (just needs segment_id,
        in narration order — same order the segments were generated).
    segment_audio_paths: {segment_id: path_to_synthesized_mp3}

    Returns the path to the final MP4: original video, audio track
    replaced by the concatenated Somali narration. Raises
    RuntimeError on any ffmpeg failure.
    """
    ordered_audio = [
        segment_audio_paths[seg["segment_id"]]
        for seg in segments
        if seg["segment_id"] in segment_audio_paths
    ]
    if not ordered_audio:
        raise RuntimeError("No synthesized audio available to render with.")

    if len(ordered_audio) == 1:
        combined_audio = ordered_audio[0]
    else:
        combined_audio = os.path.join(RENDER_DIR, f"combined_{uuid.uuid4().hex}.mp3")
        _concat_audio(ordered_audio, combined_audio)

    final_output = os.path.join(RENDER_DIR, f"final_{uuid.uuid4().hex}.mp4")
    _run([
        "ffmpeg", "-y", "-i", video_path, "-i", combined_audio,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        final_output,
    ])
    return final_output
