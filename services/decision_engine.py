"""
Rule Engine + Decision Engine — kept as SEPARATE functions on purpose
(per Abdalla's architecture request): rule_engine() is pure
deterministic timing math (no AI, no external data — same logic from
the original Timeline Sync Module spec). decision_engine() combines
that with the motion/semantic signals from the CV/AI layers to
produce the final per-segment decision.
"""


def rule_engine(scene_duration: float, voice_duration: float) -> dict:
    """
    Pure timing math. No AI, no external calls — just arithmetic.
    """
    diff = round(voice_duration - scene_duration, 2)

    if diff <= 0.3:
        return {"action": "none", "freeze_duration": 0.0}
    elif diff <= 3.0:
        return {"action": "freeze", "freeze_duration": diff}
    else:
        return {"action": "speed_adjust", "freeze_duration": 0.0}


def decision_engine(segment: dict, motion_score: float, semantic_score: float) -> dict:
    """
    segment must have: start, end, voice_duration

    Returns the fields to merge into the segment's final JSON:
        action, freeze_duration, motion_score, semantic_score, needs_review
    """
    scene_duration = segment["end"] - segment["start"]
    rule_result = rule_engine(scene_duration, segment["voice_duration"])

    action = rule_result["action"]
    freeze_duration = rule_result["freeze_duration"]

    # Motion override: freezing a high-motion shot (running, action)
    # looks broken on screen — prefer speed_adjust instead if the
    # Rule Engine suggested freeze but the shot has a lot of motion.
    if action == "freeze" and motion_score >= 60:
        action = "speed_adjust"
        freeze_duration = 0.0

    # Low semantic score doesn't change the sync decision — it flags
    # the segment for manual review instead of silently shipping a
    # possibly-mistranslated line.
    needs_review = semantic_score < 70

    return {
        "action": action,
        "freeze_duration": freeze_duration,
        "motion_score": motion_score,
        "semantic_score": semantic_score,
        "needs_review": needs_review,
    }
