"""
Gemini script generation — now receives cheap TEXT (the Whisper
transcript), not the whole video. This is the fix for the token/cost
concern: sending a ~150-word transcript costs a tiny fraction of what
sending a full video (sampled frames + audio track) would cost.

Model: gemini-3.1-flash-lite (cheapest current model, July 2026).
Gemini model names change fast — if this 404s, check
https://ai.google.dev/gemini-api/docs/models for the current cheapest
model and swap it in below.
"""
import os
import requests

_MODEL = "gemini-3.1-flash-lite"
_BASE = "https://generativelanguage.googleapis.com"

# EDIT THIS to change what Gemini is asked to do — this is the one
# place the script-generation prompt lives.
PROMPT_TEMPLATE = """\
Waxaad tahay AI kaaliye ku xeel dheer qoritaanka script-yada "movie recap" ee af-Soomaaliga ah.

HAWSHA:
Transcript-kan hoose waxaad u beddelaysaa script Soomaali ah oo si dabiici ah loo akhriyi karo cod-marinta (voice-over), ma aha turjumaad tooska ah ee eray-eray ah.

XEERAR:
1. Isticmaal Soomaali fudud, caadi ah — ma aha mid aad u qoto-dheer ama akadeemi ah.
2. Sii jir dhammaan macnaha muhiimka ah ee transcript-ka asalka ah — ha samayn sheeko aan la xidhiidhin xogta dhabta ah.
3. Qaabka "recap" — soo koobid, ma aha faahfaahin dhamaystiran hal-hal ah.
4. Soo celi qoraalka SCRIPT-KA OO KELIYA — ha ku darin cinwaanno, sharaxaad, ama qoraal markdown ah (sida ** ama #).
5. Dherer-ka script-ku ha la mid noqdo si loola jaanqaado dherer transcript-ka asalka ah.

TRANSCRIPT-KA ASALKA AH:
{transcript}
"""


def generate_somali_script(transcript: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — check your .env file.")

    prompt = PROMPT_TEMPLATE.replace("{transcript}", transcript)

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
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response shape: {response.text}") from e
