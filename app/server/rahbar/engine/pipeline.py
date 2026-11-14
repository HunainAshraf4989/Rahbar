"""Channel-independent, cancellable voice turn; dependencies are injected ports."""

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

from rahbar.contracts.models import (
    ApprovedSentence,
    ProviderMetadata,
    ReleaseContext,
    RetrievalRequest,
    TeachingContext,
)
from rahbar.contracts.ports import (
    GenerationPort,
    Normalizer,
    ResponseChecker,
    RetrievalPort,
    STTPort,
    TTSPort,
    TurnContext,
)
from rahbar.engine.audio import InputError, pcm_to_wav, validate_wav
from rahbar.engine.dummy import NORMALIZER_VERSION
from rahbar.engine.runtime import check_cancelled


@dataclass(frozen=True)
class TurnResult:
    wav: bytes
    outcome: str
    timings_ms: dict[str, float]
    providers: list[ProviderMetadata]
    transcript: str
    sentences: list[ApprovedSentence]


class Pipeline:
    def __init__(self, stt: STTPort, retrieval: RetrievalPort, generation: GenerationPort,
                 checker: ResponseChecker, normalizer: Normalizer, tts: TTSPort,
                 observer: Callable[[str, dict[str, object]], None] | None = None) -> None:
        self.stt, self.retrieval, self.generation = stt, retrieval, generation
        self.checker, self.normalizer, self.tts = checker, normalizer, tts
        self.observer = observer

    def observe(self, stage: str, data: dict[str, object]) -> None:
        if self.observer is not None:
            self.observer(stage, data)

    async def run(self, audio: bytes, teaching: TeachingContext, context: TurnContext) -> TurnResult:
        return await self._run_cancellable(audio, teaching, context)

    async def run_text(self, text: str, teaching: TeachingContext, context: TurnContext) -> TurnResult:
        return await self._run_cancellable(text, teaching, context)

    async def _run_cancellable(self, question_input: bytes | str, teaching: TeachingContext,
                               context: TurnContext) -> TurnResult:
        check_cancelled(context)
        work = asyncio.create_task(self._run(question_input, teaching, context))
        cancelled = asyncio.create_task(context.cancelled.wait())
        try:
            await asyncio.wait({work, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            check_cancelled(context)
            return await work
        finally:
            work.cancel()
            cancelled.cancel()
            await asyncio.gather(work, cancelled, return_exceptions=True)

    async def _run(self, question_input: bytes | str, teaching: TeachingContext,
                   context: TurnContext) -> TurnResult:
        timings: dict[str, float] = {}
        providers: list[ProviderMetadata] = []
        hypothesis_revision = 0
        start = perf_counter()
        if isinstance(question_input, bytes):
            validate_wav(question_input)
            transcript = await self.stt.transcribe_file(question_input, context)
            if not transcript.final or transcript.turn_id != context.turn_id:
                raise InputError("Invalid final transcript identity")
            self.observe("stt", transcript.model_dump())
            timings["stt"] = (perf_counter() - start) * 1000
            providers.append(transcript.metadata)
            hypothesis_revision = transcript.hypothesis_revision
            question = self.normalizer.canonicalize(transcript.text, "stt", NORMALIZER_VERSION)
        else:
            question = self.normalizer.canonicalize(question_input, "typed", NORMALIZER_VERSION)
        self.observe("normalization", question.model_dump())
        stage = perf_counter()
        request = RetrievalRequest(
            canonical_text=question.text, course_id=teaching.course_id,
            content_version=teaching.content_version, index_version="dummy-v1",
            unit_id=teaching.unit_id, turn_id=context.turn_id,
            hypothesis_revision=hypothesis_revision, final=True,
        )
        evidence = await self.retrieval.retrieve(request, context)
        if evidence.request != request:
            raise InputError("Retrieval returned stale or mismatched evidence")
        self.observe("retrieval", evidence.model_dump())
        timings["retrieval"] = (perf_counter() - stage) * 1000
        pcm = bytearray()
        sentences = []
        outcome = "answered"
        release = ReleaseContext(approved_prefix=[], played_prefix=[], required_warning_ids=[],
                                 reviewed_prerequisite_ids=[])
        stage = perf_counter()
        async for candidate in self.generation.generate(question, teaching, evidence, context):
            self.observe("generation", candidate.model_dump())
            check_cancelled(context)
            checked = await self.checker.check(candidate, evidence, None, release, context)
            self.observe("checker", checked.model_dump())
            if checked.output is None:
                raise InputError("Answer rejected before synthesis")
            if checked.status == "refusal":
                outcome = "refused"
            sentences.append(checked.output)
            speech = self.normalizer.prepare_speech(checked.output, NORMALIZER_VERSION)
            self.observe("speech_preparation", speech.model_dump())
            sequence = 0
            async for chunk in self.tts.synthesize(speech, context):
                check_cancelled(context)
                if (chunk.sample_rate != 24000 or chunk.audio.sentence_id != speech.sentence_id
                        or chunk.audio.chunk_sequence != sequence):
                    raise InputError("Invalid synthesized audio contract")
                sequence += 1
                pcm.extend(base64.b64decode(chunk.audio.data, validate=True))
                providers.append(chunk.metadata)
                self.observe("tts", {"metadata": chunk.metadata.model_dump(),
                                     "bytes": len(pcm), "sample_rate": chunk.sample_rate})
                if len(pcm) > 24000 * 2 * 60:
                    raise InputError("Reply exceeds maximum duration")
            if sequence == 0:
                raise InputError("Speech provider returned no audio")
        if not pcm:
            raise InputError("No approved answer audio")
        timings["answer_and_tts"] = (perf_counter() - stage) * 1000
        timings["total"] = (perf_counter() - start) * 1000
        return TurnResult(pcm_to_wav(bytes(pcm)), outcome, timings, providers,
                          question.text, sentences)
