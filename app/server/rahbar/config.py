"""The only credential loader. Never log environment values or validation inputs."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotenv import dotenv_values


@dataclass(frozen=True)
class SupabaseSettings:
    database_url: str = field(repr=False)
    url: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)


def load_supabase_settings(root: Path) -> SupabaseSettings:
    """Load migration credentials centrally; never expose values in errors or logs."""
    values = {**dotenv_values(root / ".env"), **dotenv_values(root / ".env.local"),
              **os.environ}

    def first(*names: str) -> str:
        return next((str(values[name]).strip() for name in names if values.get(name)), "")

    database_url = first("RAHBAR_DATABASE_URL", "DATABASE_URL", "SUPABASE_DB_URL",
                         "POSTGRES_URL", "DIRECT_URL", "POSTGRES_URL_NON_POOLING")
    password = first("SUPABASE_DB_PASSWORD", "DATABASE_PASSWORD", "DB_PASSWORD",
                     "POSTGRES_PASSWORD")
    url = first("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL", "VITE_SUPABASE_URL")
    if not database_url and password:
        host = urlsplit(url).hostname or ""
        if re.fullmatch(r"[a-z0-9]+\.supabase\.co", host):
            database_url = f"postgresql://postgres:{quote(password, safe='')}@db.{host}:5432/postgres"
    for placeholder in ("[YOUR-PASSWORD]", "[YOUR_PASSWORD]", "<PASSWORD>"):
        if placeholder in database_url and password:
            database_url = database_url.replace(placeholder, quote(password, safe=""))
    if database_url and not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("Expected a PostgreSQL connection URI in DATABASE_URL")
    return SupabaseSettings(database_url=database_url,
        url=url,
        secret_key=first("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY"))


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


@dataclass(frozen=True)
class RetrievalSettings:
    gemini_api_key: str = field(repr=False)
    models: tuple[str, ...] = ("gemini-2.5-flash", "gemini-2.5-flash-lite")
    judge_models: tuple[str, ...] = ("gemini-2.5-pro", "gemini-2.5-flash")


def load_retrieval_settings(root: Path) -> RetrievalSettings:
    """Text retrieval does not require STT/TTS credentials. Never print loaded values."""
    values = {**dotenv_values(root / '.env'), **dotenv_values(root / '.env.local'), **os.environ}
    key = (values.get('RAHBAR_GEMINI_API_KEY') or values.get('GEMINI_API_KEY')
           or values.get('GOOGLE_API_KEY') or '').strip()
    models = tuple(part.strip() for part in
                   (values.get('RAHBAR_RAG_MODELS') or
                    'gemini-2.5-flash,gemini-2.5-flash-lite').split(','))
    if not key:
        raise ValueError('Missing Gemini API key for text RAG')
    if not models or any(not re.fullmatch(r'[a-zA-Z0-9._-]+', model) for model in models):
        raise ValueError('Invalid RAHBAR_RAG_MODELS')
    judge_models = tuple(part.strip() for part in
                         (values.get('RAHBAR_RAG_JUDGE_MODELS') or
                          'gemini-2.5-pro,gemini-2.5-flash').split(','))
    if any(not re.fullmatch(r'[a-zA-Z0-9._-]+', model) for model in judge_models):
        raise ValueError('Invalid RAHBAR_RAG_JUDGE_MODELS')
    return RetrievalSettings(gemini_api_key=key, models=models, judge_models=judge_models)
