"""In-process boundaries; adapters own no platform persistence."""

from asyncio import Event
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from .events import AudioChunk
from .models import (
    ApprovedSentence,
    CanonicalText,
    CheckResult,
    EvidenceSet,
    ProviderMetadata,
    ReleaseContext,
    RetrievalRequest,
    SentenceCandidate,
    SpeechText,
    TeachingContext,
    TranscriptUpdate,
    VisualTarget,
)


@dataclass(frozen=True)
class TurnContext:
    session_id: str
    turn_id: str
    generation: int
    connection_epoch: str
    cancelled: Event
    history: tuple[tuple[str, str], ...] = ()


class STTPort(Protocol):
    def transcribe_stream(
        self, chunks: AsyncIterator[bytes], context: TurnContext
    ) -> AsyncIterator[TranscriptUpdate]: ...
    async def transcribe_file(self, audio: bytes, context: TurnContext) -> TranscriptUpdate: ...


class Normalizer(Protocol):
    def canonicalize(self, text: str, boundary: str, version: str) -> CanonicalText: ...
    def prepare_speech(self, approved: ApprovedSentence, version: str) -> SpeechText: ...


class RetrievalPort(Protocol):
    async def retrieve(self, request: RetrievalRequest, context: TurnContext) -> EvidenceSet: ...


class GenerationPort(Protocol):
    def generate(
        self,
        question: CanonicalText,
        teaching: TeachingContext,
        evidence: EvidenceSet,
        context: TurnContext,
    ) -> AsyncIterator[SentenceCandidate]: ...


class ResponseChecker(Protocol):
    async def check(
        self,
        candidate: SentenceCandidate,
        evidence: EvidenceSet,
        visual: VisualTarget | None,
        release: ReleaseContext,
        context: TurnContext,
    ) -> CheckResult: ...


@dataclass(frozen=True)
class SynthesisChunk:
    audio: AudioChunk
    metadata: ProviderMetadata
    sample_rate: int


class TTSPort(Protocol):
    def synthesize(
        self, text: SpeechText, context: TurnContext
    ) -> AsyncIterator[SynthesisChunk]: ...
