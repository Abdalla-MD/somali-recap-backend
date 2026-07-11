"""
Real Somali voice generation using edge-tts — the same tool/approach
from Abdalla's original command:
  python -m edge_tts --voice so-SO-MuuseNeural --rate="+20%" --pitch="-10%" ...
"""
import os
import uuid
import edge_tts

OUTPUT_DIR = "generated_audio"
os.makedirs(OUTPUT_DIR, exist_ok=True)

VOICE_MAP = {
    "muuse": "so-SO-MuuseNeural",
    "ubax": "so-SO-UbaxNeural",
}


def _format_percent(value: float) -> str:
    rounded = round(value)
    sign = "+" if rounded >= 0 else ""
    return f"{sign}{rounded}%"


def _format_hz(value: float) -> str:
    """edge-tts's Communicate() pitch parameter requires Hz format
    (e.g. "+20Hz"), NOT percent — unlike rate/volume which use "%".
    This was the bug causing every synthesis call to fail with
    "Invalid pitch '+0%'"."""
    rounded = round(value)
    sign = "+" if rounded >= 0 else ""
    return f"{sign}{rounded}Hz"


async def synthesize(text: str, voice: str, speed: float = 1.0, pitch: float = 0.0) -> str:
    """
    speed: 0.5 - 2.0 (1.0 = normal) — converted to edge-tts's rate
           percentage, e.g. speed=1.2 -> "+20%"
    pitch: -50 - +50 (from the Flutter UI slider) — converted to Hz,
           e.g. pitch=20 -> "+20Hz"

    Returns the path to the generated mp3 file.
    """
    voice_id = VOICE_MAP.get(voice, VOICE_MAP["muuse"])
    rate_str = _format_percent((speed - 1.0) * 100)
    pitch_str = _format_hz(pitch)

    filename = f"{uuid.uuid4().hex}.mp3"
    filepath = os.path.join(OUTPUT_DIR, filename)

    communicate = edge_tts.Communicate(text, voice_id, rate=rate_str, pitch=pitch_str)
    await communicate.save(filepath)
    return filepath
