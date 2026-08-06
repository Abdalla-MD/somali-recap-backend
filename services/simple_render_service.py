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


def _get_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr}")
    return float(result.stdout.strip())


def render_simple_video(video_path: str, segments: list, segment_audio_paths: dict) -> str:
    """
    segments: ordered list of segment dicts (just needs segment_id,
        in narration order — same order the segments were generated).
    segment_audio_paths: {segment_id: path_to_synthesized_mp3}

    Returns the path to the final MP4. Raises RuntimeError on any
    ffmpeg failure.

    "GLOBAL SPEED MATCH" ENGINE (per Abdalla's request): instead of
    freezing/padding to cover a duration gap, the whole video's
    playback speed is scaled ONCE (via the setpts filter) so its
    total duration exactly matches the narration's total duration.
    Shorter audio -> video plays a bit faster. Longer audio -> video
    plays a bit slower. This is a single whole-video encode — no
    per-segment work, no freeze-frame generation, no concat — simpler
    and cheaper than the earlier "Simple Extend Engine".

    The speed factor is clamped to 0.6-1.8x so a very large mismatch
    doesn't produce an absurdly fast/slow, unwatchable result — if
    the natural factor falls outside that range, it's clamped and a
    small residual duration mismatch is accepted (handled by
    -shortest as a safety net, same as before).
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

    video_duration = _get_duration(video_path)
    audio_duration = _get_duration(combined_audio)

    raw_factor = audio_duration / video_duration if video_duration > 0 else 1.0
    speed_factor = max(0.6, min(1.8, raw_factor))

    if abs(speed_factor - 1.0) < 0.02:
        # Close enough already — skip the re-encode entirely, stay
        # on the cheap -c:v copy path.
        video_for_mux = video_path
    else:
        adjusted_path = os.path.join(RENDER_DIR, f"speedmatch_{uuid.uuid4().hex}.mp4")
        _run([
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"setpts={speed_factor:.4f}*PTS",
            "-an",  # original audio isn't used anyway — replaced with the Somali narration
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            adjusted_path,
        ])
        video_for_mux = adjusted_path

    final_output = os.path.join(RENDER_DIR, f"final_{uuid.uuid4().hex}.mp4")
    _run([
        "ffmpeg", "-y", "-i", video_for_mux, "-i", combined_audio,
        "-map", "0:v", "-map", "1:a",
        # video_for_mux is already a complete, properly-encoded file
        # by this point either way (either untouched original, or
        # already re-encoded once by the setpts step above) — copy
        # here always, re-encoding it again would be wasted work.
        "-c:v", "copy",
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-shortest",
        final_output,
    ])
    return final_output
