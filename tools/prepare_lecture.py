"""Bake the draft relay demonstration with the centrally configured ElevenLabs voice."""

import asyncio
from pathlib import Path

import httpx
from rahbar.config import load_settings
from rahbar.contracts.ports import TurnContext
from rahbar.engine.bake import bake_lesson
from rahbar.engine.lesson import relay_asset, relay_lesson
from rahbar.engine.runtime import ProviderError
from rahbar.providers.elevenlabs import ElevenLabs


async def main() -> None:
    settings = load_settings()
    root = Path.cwd()
    lesson = relay_lesson(relay_asset(root), root)
    async with httpx.AsyncClient() as client:
        baked = await bake_lesson(lesson, ElevenLabs(settings, client), settings,
                                  root / ".cache/relay-lecture",
                                  TurnContext("bake", "bake", 0, "bake", asyncio.Event()))
    print(f"Saved {len(baked.sentences)} draft lesson clips to .cache/relay-lecture/lesson.json")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ProviderError as exc:
        raise SystemExit(exc.failure.model_dump_json()) from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
