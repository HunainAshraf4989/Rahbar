"""Batch ElevenLabs adapters implementing the existing STT/TTS ports."""

import base64
from collections.abc import AsyncIterator
from time import perf_counter

import httpx

from rahbar.config import Settings
from rahbar.contracts.events import AudioChunk
from rahbar.contracts.models import ProviderMetadata, SpeechText, TranscriptUpdate
from rahbar.contracts.ports import SynthesisChunk, TurnContext
from rahbar.engine.runtime import ProviderError, check_cancelled

BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabs:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    async def _post(self, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = await self.client.post(
                BASE_URL + path,
                headers={"xi-api-key": self.settings.api_key},
                timeout=self.settings.timeout_seconds,
                **kwargs,  # type: ignore[arg-type]
            )
        except httpx.TimeoutException:
            raise ProviderError("timeout") from None
        except httpx.RequestError:
            raise ProviderError() from None
        if response.is_error:
            # Never propagate a provider body: it can contain text or credentials.
            status = None
            try:
                body = response.json()
                detail = body.get("detail") if isinstance(body, dict) else None
                if isinstance(detail, dict):
                    status = detail.get("status")
            except ValueError:
                pass
            if status == "quota_exceeded":
                code = "quota_exhausted"
            elif response.status_code in {401, 403}:
                code = "authentication"
            elif response.status_code == 429:
                code = "throttled"
            else:
                code = "unavailable"
            raise ProviderError(code)
        return response

    async def transcribe_file(self, audio: bytes, context: TurnContext) -> TranscriptUpdate:
        check_cancelled(context)
        start = perf_counter()
        response = await self._post(
            "/speech-to-text", files={"file": ("utterance.wav", audio, "audio/wav")},
            data={"model_id": self.settings.stt_model, "language_code": "urd",
                  "tag_audio_events": "false", "diarize": "false"},
        )
        check_cancelled(context)
        try:
            body = response.json()
            text = body["text"]
            if not isinstance(text, str) or len(text) > 10000:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise ProviderError() from None
        return TranscriptUpdate(
            turn_id=context.turn_id, hypothesis_revision=0, text=text, final=True,
            metadata=ProviderMetadata(provider="elevenlabs", model=self.settings.stt_model,
                                      mock=False, language="urd",
                                      timings_ms={"stt": (perf_counter() - start) * 1000}),
        )

    async def transcribe_stream(
        self, chunks: AsyncIterator[bytes], context: TurnContext
    ) -> AsyncIterator[TranscriptUpdate]:
        # Do not pretend buffered batch recognition supplies partial transcripts.
        raise NotImplementedError("Batch adapter; realtime STT is not implemented")
        yield  # pragma: no cover

    async def synthesize(
        self, text: SpeechText, context: TurnContext
    ) -> AsyncIterator[SynthesisChunk]:
        check_cancelled(context)
        start = perf_counter()
        response = await self._post(
            f"/text-to-speech/{self.settings.voice_id}",
            params={"output_format": "pcm_24000"},
            json={"text": text.text, "model_id": self.settings.tts_model, "language_code": "ur"},
        )
        check_cancelled(context)
        pcm = response.content
        if (not pcm or len(pcm) % 2 or len(pcm) > 24000 * 2 * 60
                or response.headers.get("content-type", "").split(";")[0]
                not in {"audio/pcm", "audio/x-pcm", "audio/basic", "application/octet-stream"}):
            raise ProviderError()
        yield SynthesisChunk(
            audio=AudioChunk(stream_id=context.turn_id, chunk_sequence=0,
                             sentence_id=text.sentence_id, data=base64.b64encode(pcm).decode()),
            metadata=ProviderMetadata(provider="elevenlabs", model=self.settings.tts_model,
                                      mock=False, language="ur",
                                      timings_ms={"tts": (perf_counter() - start) * 1000}),
            sample_rate=24000,
        )
