"""
Gemini script generation — CHANGED for Phase 2A: now segment-aware.

Instead of asking Gemini for one big block of Somali prose, we send
the numbered Whisper segments and ask for ONE Somali line per
segment_id (still recap-style/concise within each line, but keeping
1:1 alignment with the original timestamps). This is what makes
downstream Semantic Score, Voice Duration, and the Decision Engine
possible — you can't sync what you can't align.

To keep token usage down, Gemini only returns {segment_id,
somali_text} pairs — Python re-attaches start/end/original_text from
the Whisper segments afterward, rather than asking Gemini to echo
that data back.
"""
import json
import os
import re

import requests

_MODEL = "gemini-3.1-flash-lite"
_BASE = "https://generativelanguage.googleapis.com"

# EDIT THIS to change what Gemini is asked to do.
PROMPT_TEMPLATE = """\
Waxaad tahay AI kaaliye ku xeel dheer qoritaanka script-yada "movie recap" ee af-Soomaaliga ah.

HAWSHA:
Hoos waxaa ku qoran liis segments ah (sentences waqti leh) oo ka socda muuqaal. Segment kastaba wuxuu leeyahay segment_id iyo qoraalkiisa asalka ah.

Segment kasta u qor SADARKA SOOMAALI AH oo ku habboon (recap-style, gaaban oo dabiici ah, ma aha turjumaad hal-eray-eray ah), adigoo isku daya inaad sii jirto macnaha muhiimka ah.

XEERAR:
1. Segment kasta waa inuu leeyahay SADARKA SOOMAALI AH oo gaar u ah — ha isku darin laba segment, ha ka boodin segment kasta.
2. Isticmaal Soomaali fudud, caadi ah.
3. Soo celi JSON OO KELIYA — array leh {{"segment_id": N, "somali_text": "..."}} — ha ku darin qoraal kale, cinwaan, ama markdown fences (```).
4. Tirada segments-ka soo celiyaa waa inay la mid tahay tirada segments-ka la siiyay.

SEGMENTS-KA ASALKA AH:
{segments_list}
"""


def _build_segments_list_text(segments: list) -> str:
    lines = []
    for seg in segments:
        lines.append(f'{seg["segment_id"]}. {seg["original_text"]}')
    return "\n".join(lines)


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def generate_somali_segments(whisper_segments: list) -> list:
    """
    whisper_segments: [{"start": 0.0, "end": 4.2, "text": "..."}, ...]

    Returns the Phase 2A structured segment list:
        [
          {
            "segment_id": 1,
            "scene_id": None,          # filled in later by Scene Detection
            "original_text": "...",
            "somali_text": "...",
            "start": 0.0,
            "end": 4.2,
            "version": 1,
            "status": "draft"
          },
          ...
        ]
    Raises RuntimeError on any failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — check your .env file.")

    # Attach segment_id (1-indexed) to each Whisper segment first.
    indexed = [
        {"segment_id": i + 1, "original_text": seg["text"], "start": seg["start"], "end": seg["end"]}
        for i, seg in enumerate(whisper_segments)
    ]

    prompt = PROMPT_TEMPLATE.format(segments_list=_build_segments_list_text(indexed))

    response = requests.post(
        f"{_BASE}/v1beta/models/{_MODEL}:generateContent",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )

    if response.status_code != 200:
        raise RuntimeError(f"Gemini API error {response.status_code}: {response.text}")

    data = response.json()
    try:
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response shape: {response.text}") from e

    cleaned = _strip_markdown_fences(raw_text)
    try:
        somali_lines = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini did not return valid JSON: {raw_text}") from e

    somali_by_id = {item["segment_id"]: item["somali_text"] for item in somali_lines}

    result = []
    for seg in indexed:
        sid = seg["segment_id"]
        if sid not in somali_by_id:
            # Gemini skipped a segment — fall back to the original
            # text rather than silently dropping the segment (keeps
            # the timeline complete; better to review than to lose a
            # chunk of video).
            somali_text = seg["original_text"]
        else:
            somali_text = somali_by_id[sid]

        result.append({
            "segment_id": sid,
            "scene_id": None,
            "original_text": seg["original_text"],
            "somali_text": somali_text,
            "start": seg["start"],
            "end": seg["end"],
            "version": 1,
            "status": "draft",
        })

    return result
