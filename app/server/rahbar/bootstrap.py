"""Composition root: change adapters here, not in the orchestration flow."""

import httpx

from rahbar.config import Settings
from rahbar.contracts.models import Passage, TeachingContext
from rahbar.engine.dummy import (
    BasicNormalizer,
    DeterministicGeneration,
    DummyChecker,
    DummyRetrieval,
)
from rahbar.engine.pipeline import Pipeline
from rahbar.providers.elevenlabs import ElevenLabs
from rahbar.providers.gemini import GeminiGeneration
from rahbar.retrieval.relay import LocalRelayRetrieval, RelayChecker, RelayNormalizer


def build_pipeline(settings: Settings, client: httpx.AsyncClient,
                   passages: list[Passage] | None = None) -> tuple[Pipeline, TeachingContext]:
    speech = ElevenLabs(settings, client)
    if settings.answer_mode == "gemini":
        return (Pipeline(speech, LocalRelayRetrieval(passages), GeminiGeneration(settings, client),
                         RelayChecker(passages), RelayNormalizer(passages), speech),
                TeachingContext(course_id="relay-demo", content_version="relay-demo-v1",
                                unit_id="relay", lesson_id="relay-demo", mode="tutor"))
    return (Pipeline(speech, DummyRetrieval(), DeterministicGeneration(), DummyChecker(),
                     BasicNormalizer(), speech),
            TeachingContext(course_id="dummy", content_version="dummy-v1", unit_id="dummy",
                            lesson_id="dummy", mode="tutor"))
