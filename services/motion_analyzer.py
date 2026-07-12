"""
Motion Analyzer — cheap, real computer vision (NOT machine learning):
samples a handful of frames across a time range and measures how much
they differ from each other (mean absolute pixel difference on a
downscaled grayscale copy). High score = lots of movement (running,
action) → freeze would look broken. Low score = mostly static
(dialogue, close-up) → freeze looks natural.

This is a heuristic approximation, not a precise optical-flow motion
measurement — good enough to drive the freeze/no-freeze decision
without needing a heavy ML model.
"""
import cv2
import numpy as np


def motion_score(video_path: str, start: float, end: float, samples: int = 6) -> float:
    """
    Returns a 0-100 motion score for the [start, end] time range.
    Higher = more movement between sampled frames.
    Returns 0.0 if the range is too short to sample or frames can't
    be read (fails soft rather than raising — a bad motion score
    shouldn't block the whole render).
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return 0.0

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        duration = max(end - start, 0.1)
        n = max(2, samples)
        sample_times = [start + duration * i / (n - 1) for i in range(n)]

        frames = []
        for t in sample_times:
            frame_no = int(t * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ok, frame = cap.read()
            if ok:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, (160, 90))
                frames.append(gray.astype(np.float32))

        if len(frames) < 2:
            return 0.0

        diffs = [
            float(np.mean(np.abs(frames[i] - frames[i - 1])))
            for i in range(1, len(frames))
        ]
        avg_diff = float(np.mean(diffs))

        # Empirically, an average pixel difference of ~40+ (on a
        # 0-255 scale) already reads as high motion — scale/clamp to
        # a 0-100 score against that.
        score = min(100.0, (avg_diff / 40.0) * 100.0)
        return round(score, 1)
    finally:
        cap.release()
