"""
Scene Detection using PySceneDetect (ContentDetector) — pure
computer-vision shot-cut detection (compares HSV differences between
adjacent frames), no AI/ML model or API call needed. This finds where
the camera cuts to a new shot; the Sync Engine uses these boundaries
to assign scene_id to each script segment (by timestamp overlap).
"""
from scenedetect import detect, ContentDetector


def detect_scenes(video_path: str) -> list:
    """
    Returns: [{"scene_id": 1, "start": 0.0, "end": 4.2}, ...]
    Returns an empty list if no cuts were found (e.g. a single
    continuous shot) — callers should treat that as "everything is
    one scene" rather than an error.
    Raises RuntimeError on failure (corrupt file, unreadable video).
    """
    try:
        scene_list = detect(video_path, ContentDetector())
    except Exception as e:
        raise RuntimeError(f"Scene detection failed: {e}") from e

    scenes = []
    for i, (start_tc, end_tc) in enumerate(scene_list):
        scenes.append({
            "scene_id": i + 1,
            "start": round(start_tc.get_seconds(), 2),
            "end": round(end_tc.get_seconds(), 2),
        })
    return scenes
