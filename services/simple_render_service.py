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

    DURATION MATCHING ("Simple Extend Engine"): rather than cutting
    whichever track is shorter (the old -shortest behavior, which was
    chopping off narration whenever the Somali audio ran longer than
    the source video — very common, since Somali translations often
    take longer to say), this now extends the SHORTER track to match
    the longer one:
      - audio longer -> video gets a frozen-last-frame extension
        (tpad filter) — one re-encode of the whole video, not per
        segment, so it's still much cheaper than the old pipeline.
      - video longer -> audio gets silence padding (apad filter) —
        cheap, audio-only.
      - close enough (<=1 sec difference) -> no extra encode at all,
        same -c:v copy fast path as before.
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
    diff = audio_duration - video_duration  # positive = audio longer

    video_for_mux = video_path
    audio_for_mux = combined_audio
    use_shortest = True

    if diff > 1.0:
        # Audio meaningfully longer — extend the video by holding its
        # last frame, so no narration gets cut off. This is the one
        # case that costs a real re-encode (tpad has to touch the
        # whole clip), but it's a SINGLE whole-video encode, not
        # 4-per-segment like the old pipeline.
        extended_path = os.path.join(RENDER_DIR, f"extended_{uuid.uuid4().hex}.mp4")
        _run([
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"tpad=stop_mode=clone:stop_duration={diff:.2f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            extended_path,
        ])
        video_for_mux = extended_path
        use_shortest = False
    elif diff < -1.0:
        # Video meaningfully longer — pad the audio with silence
        # instead of cutting the video short. Audio-only, cheap.
        padded_audio_path = os.path.join(RENDER_DIR, f"padded_audio_{uuid.uuid4().hex}.mp3")
        _run([
            "ffmpeg", "-y", "-i", combined_audio,
            "-af", f"apad=pad_dur={abs(diff):.2f}",
            padded_audio_path,
        ])
        audio_for_mux = padded_audio_path
        use_shortest = False
    # else: within 1 second — close enough, no extra encode needed.

    final_output = os.path.join(RENDER_DIR, f"final_{uuid.uuid4().hex}.mp4")
    mux_cmd = [
        "ffmpeg", "-y", "-i", video_for_mux, "-i", audio_for_mux,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac",
        "-movflags", "+faststart",
    ]
    if use_shortest:
        mux_cmd.append("-shortest")
    mux_cmd.append(final_output)
    _run(mux_cmd)
    return final_output
