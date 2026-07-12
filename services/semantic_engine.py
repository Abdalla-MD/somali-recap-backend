"""
Semantic Engine — per-segment text-only comparison (original language
vs Somali) via Gemini, checking meaning was preserved. Genuinely
cheap: text in, text out (this is exactly why Phase 2A moved away
from sending Gemini whole videos — same principle applies here).
"""
import json
import os
import re

import requests

_MODEL = "gemini-3.1-flash-lite"
_BASE = "https://generativelanguage.googleapis.com"

PROMPT_TEMPLATE = """\
Waxaad tahay AI kaaliye xaqiijiya turjumaadda. Hoos waxaa ku qoran liis xiriiro ah — qoraal asalka ah iyo turjumaadiisa Soomaaliga ah.

Segment kasta u qiimee sida ay macnaha ugu dhow yihiin (0-100, 100 = macnaha oo dhan sax, 0 = macno kale gebi ahaanba).

Soo celi JSON OO KELIYA — array leh {{"segment_id": N, "score": N}} — ha ku darin qoraal kale, cinwaan, ama markdown fences.

SEGMENTS:
{pairs}
"""


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def semantic_scores(segments: list) -> dict:
    """
    segments: [{"segment_id": 1, "original_text": "...", "somali_text": "..."}, ...]
    Returns: {segment_id: score (0-100)}
    Raises RuntimeError on failure — callers should treat that as "no
    semantic data available" (e.g. default everyone to a neutral
    score) rather than blocking the whole render on it.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    pairs_text = "\n".join(
        f'{s["segment_id"]}. ASALKA: {s["original_text"]} | SOOMAALI: {s["somali_text"]}'
        for s in segments
    )
    prompt = PROMPT_TEMPLATE.format(pairs=pairs_text)

    response = requests.post(
        f"{_BASE}/v1beta/models/{_MODEL}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
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
        scores_list = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini did not return valid JSON: {raw_text}") from e

    return {item["segment_id"]: item["score"] for item in scores_list}
