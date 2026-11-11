"""The only credential loader. Never log environment values or validation inputs."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    voice_id: str
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = "gemini-2.5-flash"
    answer_mode: str = "dummy"
    stt_model: str = "scribe_v2"
    tts_model: str = "eleven_v3"
    timeout_seconds: float = 45.0
    max_audio_bytes: int = 960_044  # thirty seconds of PCM16 mono at 16 kHz + WAV header


def load_settings(env_file: Path = Path(".env")) -> Settings:
    values = {**dotenv_values(env_file), **os.environ}
    key = values.get("RAHBAR_ELEVENLABS_API_KEY") or values.get("ELEVENLABS_API_KEY")
    voice = values.get("RAHBAR_ELEVENLABS_VOICE_ID") or values.get("ELEVENLABS_VOICE_ID") or "JBFqnCBsd6RMkjVDRZzb"
    if not key or not key.strip():
        raise ValueError("Missing RAHBAR_ELEVENLABS_API_KEY (or ELEVENLABS_API_KEY)")
    if not voice or not voice.strip():
        raise ValueError("Missing RAHBAR_ELEVENLABS_VOICE_ID (or ELEVENLABS_VOICE_ID)")
    # Voice IDs are URL path segments, never URLs or arbitrary paths.
    if not voice.isalnum():
        raise ValueError("Invalid ElevenLabs voice ID")
    mode = values.get("RAHBAR_ANSWER_MODE") or "dummy"
    if mode not in {"dummy", "gemini"}:
        raise ValueError("RAHBAR_ANSWER_MODE must be dummy or gemini")
    gemini_key = (values.get("RAHBAR_GEMINI_API_KEY") or values.get("GEMINI_API_KEY")
                  or values.get("GOOGLE_API_KEY") or "")
    if mode == "gemini" and not gemini_key.strip():
        raise ValueError("Missing GEMINI_API_KEY for Gemini mode")
    model = values.get("RAHBAR_GEMINI_MODEL") or "gemini-2.5-flash"
    if not all(c.isalnum() or c in "-._" for c in model):
        raise ValueError("Invalid Gemini model ID")
    return Settings(api_key=key.strip(), voice_id=voice, gemini_api_key=gemini_key.strip(),
                    gemini_model=model, answer_mode=mode)
