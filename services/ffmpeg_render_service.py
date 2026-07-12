"""
FFmpeg Render — Cinematic Freeze Engine + final video assembly.

Builds the final video segment-by-segment: each segment becomes a
self-contained clip (trim + optional freeze-with-zoom/shake + that
segment's own Somali audio muxed in), then all clips are concatenated
end-to-end. Self-contained clips are easier to reason about than
juggling one global video timeline + one global audio timeline.

IMPORTANT: every intermediate clip is normalized to the same
resolution/fps (see _CANONICAL_*) immediately after trimming — this
is what makes the final concat step safe. Without this, a freeze
clip (rendered at a fixed size by the zoompan filter) and a trimmed
clip (at the source video's native resolution) would very likely not
concatenate cleanly.
"""
import os
import subprocess
import uuid

RENDER_DIR = "render_tmp"
os.makedirs(RENDER_DIR, exist_ok=True)

_CANONICAL_WIDTH = 1280
_CANONICAL_HEIGHT = 720
_CANONICAL_FPS = 25


def _run(cmd: list):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg command failed: {' '.join(cmd)}\n{result.stderr}")


def _get_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr}")
    return float(result.stdout.strip())


def _trim_and_normalize(video_path: str, start: float, end: float, out_path: str):
    """Trims [start, end] and scales/pads to the canonical resolution
    + fps so every clip is guaranteed byte-compatible for concat
    later, regardless of the source video's native resolution."""
    scale_pad = (
        f"scale={_CANONICAL_WIDTH}:{_CANONICAL_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={_CANONICAL_WIDTH}:{_CANONICAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={_CANONICAL_FPS}"
    )
    _run([
        "ffmpeg", "-y", "-i", video_path,
        "-ss", str(start), "-to", str(end),
        "-an", "-vf", scale_pad,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        out_path,
    ])


def _freeze_extension(source_clip_path: str, duration: float, out_path: str):
    """
    Grabs the last frame of an already-normalized clip and extends it
    for `duration` seconds with a subtle zoom + micro-shake
    (Cinematic Freeze Engine), so the freeze doesn't look like a dead
    paused image. source_clip_path is expected to already be at
    canonical resolution (called after _trim_and_normalize).
    """
    frame_path = out_path.replace(".mp4", "_frame.png")
    _run(["ffmpeg", "-y", "-sseof", "-0.2", "-i", source_clip_path, "-vframes", "1", frame_path])

    frame_count = max(1, int(duration * _CANONICAL_FPS))
    zoompan = (
        "zoompan=z='min(zoom+0.0015,1.08)':"
        "x='iw/2-(iw/zoom/2)+sin(on/7)*2':"
        "y='ih/2-(ih/zoom/2)+cos(on/7)*2':"
        f"d={frame_count}:s={_CANONICAL_WIDTH}x{_CANONICAL_HEIGHT}:fps={_CANONICAL_FPS}"
    )
    try:
        _run([
            "ffmpeg", "-y", "-loop", "1", "-i", frame_path,
            "-vf", zoompan,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            out_path,
        ])
    finally:
        if os.path.exists(frame_path):
            os.remove(frame_path)


def _concat_videos(video_paths: list, out_path: str, has_audio: bool = False):
    """Re-encodes during concat (via filter_complex) rather than
    stream-copy — slower, but far more robust than the concat
    demuxer's strict "all inputs must have byte-identical params"
    requirement, which is easy to violate by accident.

    has_audio=False: video-only concat (used for trim+freeze, which
        are both silent clips at this stage).
    has_audio=True: preserves the audio stream too (used for the
        FINAL concat of fully-muxed segment clips — video-only concat
        here would silently drop the Somali audio from the output,
        which was a real bug caught before this ever shipped).
    """
    inputs = []
    for p in video_paths:
        inputs += ["-i", p]

    if has_audio:
        stream_refs = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(len(video_paths)))
        filter_complex = f"{stream_refs}concat=n={len(video_paths)}:v=1:a=1[outv][outa]"
        map_args = ["-map", "[outv]", "-map", "[outa]"]
        codec_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac"]
    else:
        stream_refs = "".join(f"[{i}:v:0]" for i in range(len(video_paths)))
        filter_complex = f"{stream_refs}concat=n={len(video_paths)}:v=1:a=0[outv]"
        map_args = ["-map", "[outv]"]
        codec_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]

    _run(["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, *map_args, *codec_args, out_path])


def _adjust_audio_tempo(audio_path: str, tempo: float, out_path: str):
    # atempo only supports 0.5-2.0 per filter instance.
    tempo = max(0.5, min(2.0, tempo))
    _run(["ffmpeg", "-y", "-i", audio_path, "-filter:a", f"atempo={tempo}", out_path])


def _mux_video_audio(video_path: str, audio_path: str, out_path: str):
    _run([
        "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        out_path,
    ])


def render_final_video(video_path: str, segments: list, segment_audio_paths: dict) -> str:
    """
    segments: Decision-Engine-annotated segments, each with
        segment_id, start, end, action, freeze_duration, voice_duration
    segment_audio_paths: {segment_id: path_to_synthesized_mp3}

    Returns the path to the final rendered MP4. Raises RuntimeError
    on any ffmpeg failure (caller should surface this as a render
    error, not silently produce a broken video).
    """
    clip_paths = []

    for seg in segments:
        sid = seg["segment_id"]
        base = os.path.join(RENDER_DIR, f"seg_{sid}_{uuid.uuid4().hex}")

        trimmed_path = f"{base}_trim.mp4"
        _trim_and_normalize(video_path, seg["start"], seg["end"], trimmed_path)

        video_for_audio = trimmed_path
        if seg.get("action") == "freeze" and seg.get("freeze_duration", 0) > 0:
            freeze_path = f"{base}_freeze.mp4"
            _freeze_extension(trimmed_path, seg["freeze_duration"], freeze_path)
            extended_path = f"{base}_extended.mp4"
            _concat_videos([trimmed_path, freeze_path], extended_path)
            video_for_audio = extended_path

        audio_path = segment_audio_paths.get(sid)
        if audio_path is None:
            clip_paths.append(video_for_audio)
            continue

        final_audio_path = audio_path
        if seg.get("action") == "speed_adjust":
            video_duration = _get_duration(video_for_audio)
            voice_duration = seg.get("voice_duration", video_duration)
            if video_duration > 0 and voice_duration > 0:
                tempo = voice_duration / video_duration
                tempo_path = f"{base}_tempo.mp3"
                _adjust_audio_tempo(audio_path, tempo, tempo_path)
                final_audio_path = tempo_path

        clip_out = f"{base}_final.mp4"
        _mux_video_audio(video_for_audio, final_audio_path, clip_out)
        clip_paths.append(clip_out)

    if not clip_paths:
        raise RuntimeError("No segments to render.")

    final_output = os.path.join(RENDER_DIR, f"final_{uuid.uuid4().hex}.mp4")
    _concat_videos(clip_paths, final_output, has_audio=True)
    return final_output
