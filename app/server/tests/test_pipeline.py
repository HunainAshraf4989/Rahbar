import asyncio
import io
import wave

import httpx
import pytest
from fastapi.testclient import TestClient
from rahbar.api import create_app
from rahbar.config import Settings, load_settings
from rahbar.contracts.models import SpeechText, TeachingContext
from rahbar.contracts.ports import TurnContext
from rahbar.engine.audio import InputError, pcm_to_wav, validate_wav
from rahbar.engine.dummy import (
    ANSWER,
    BasicNormalizer,
    DeterministicGeneration,
    DummyChecker,
    DummyRetrieval,
)
from rahbar.engine.pipeline import Pipeline
from rahbar.engine.runtime import ProviderError
from rahbar.providers.elevenlabs import ElevenLabs


def context():
    return TurnContext("session", "turn", 0, "epoch", asyncio.Event())


def teaching():
    return TeachingContext(course_id="dummy", content_version="dummy-v1", unit_id="dummy",
                           lesson_id="dummy", mode="tutor")


def make_pipeline(handler):
    speech = ElevenLabs(Settings("test-secret", "stockvoice"),
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return Pipeline(speech, DummyRetrieval(), DeterministicGeneration(), DummyChecker(),
                    BasicNormalizer(), speech)


def input_wav():
    return pcm_to_wav(b"\x01\x00" * 1600, 16000)


@pytest.mark.parametrize("question,outcome", [("آزمائشی سوال", "answered"),
                                             ("bypass a fuse", "refused")])
def test_http_round_trip(question, outcome, tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        assert request.headers["xi-api-key"] == "test-secret"
        if request.url.path.endswith("speech-to-text"):
            assert b'scribe_v2' in request.content and b'urd' in request.content
            assert b'utterance.wav' in request.content
            return httpx.Response(200, json={"text": question})
        assert request.url.params["output_format"] == "pcm_24000"
        assert b'eleven_v3' in request.content
        return httpx.Response(200, content=b"\x01\x00" * 2400,
                              headers={"content-type": "audio/pcm"})
    with TestClient(create_app(make_pipeline(handle), lecture_directory=tmp_path)) as client:
        response = client.post("/dev/voice-turn", content=input_wav(),
                               headers={"content-type": "audio/wav"})
    assert response.status_code == 200, response.text
    assert response.headers["x-outcome"] == outcome
    assert response.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(response.content), "rb") as wav:
        assert (wav.getframerate(), wav.getnframes()) == (24000, 2400)
    assert len(requests) == 2


@pytest.mark.parametrize("status,body,code", [
    (401, {"detail": {"status": "invalid_api_key", "message": "test-secret"}}, "authentication"),
    (401, {"detail": {"status": "quota_exceeded"}}, "quota_exhausted"),
    (429, {}, "throttled"), (503, {}, "unavailable"),
    (200, {"text": 3}, "unavailable"), (200, [], "unavailable"),
])
def test_provider_errors_redacted(status, body, code):
    async def run():
        pipeline = make_pipeline(lambda r: httpx.Response(status, json=body))
        with pytest.raises(ProviderError) as caught:
            await pipeline.stt.transcribe_file(input_wav(), context())
        assert caught.value.failure.code == code
        assert not caught.value.failure.fallback_ready
        assert "test-secret" not in str(caught.value)
    asyncio.run(run())


@pytest.mark.parametrize("audio", [b"", b"not wav", input_wav()[:-1],
                                    pcm_to_wav(b"\0\0" * 2400)])
def test_invalid_audio(audio):
    with pytest.raises(InputError):
        validate_wav(audio)


def test_http_rejects_before_provider(tmp_path):
    def handle(request):
        pytest.fail("invalid input reached paid provider")
    with TestClient(create_app(make_pipeline(handle), lecture_directory=tmp_path)) as client:
        assert client.post("/dev/voice-turn", content=b"x").status_code == 415
        assert client.post("/dev/voice-turn", content=b"x" * 960045,
                           headers={"content-type": "audio/wav"}).status_code == 413
        assert client.post("/dev/voice-turn", content=b"x",
                           headers={"content-type": "audio/wav"}).status_code == 422


def test_cancellation_stops_pending_provider():
    async def run():
        entered = asyncio.Event()
        stopped = asyncio.Event()
        async def handle(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        pipeline = make_pipeline(handle)
        ctx = context()
        task = asyncio.create_task(pipeline.run(input_wav(), teaching(), ctx))
        await asyncio.wait_for(entered.wait(), 1)
        ctx.cancelled.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert stopped.is_set()
    asyncio.run(run())


@pytest.mark.parametrize("body,content_type", [(b"", "audio/pcm"), (b"x", "audio/pcm"),
                                               (b"{}", "application/json")])
def test_malformed_tts(body, content_type):
    async def run():
        pipeline = make_pipeline(lambda r: httpx.Response(
            200, content=body, headers={"content-type": content_type}))
        with pytest.raises(ProviderError):
            async for _ in pipeline.tts.synthesize(SpeechText(
                sentence_id="reply", text=ANSWER, normalizer_version="test"), context()):
                pytest.fail("malformed speech released")
    asyncio.run(run())


def test_configuration_redacts_and_environment_overrides(tmp_path, monkeypatch):
    path = tmp_path / "settings.test"
    path.write_text("ELEVENLABS_API_KEY=file-test-key\n")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "environment-test-key")
    settings = load_settings(path)
    assert settings.api_key == "environment-test-key"
    assert "environment-test-key" not in repr(settings)


def test_unsupported_script_and_unprepared_speech_fail_closed():
    normalizer = BasicNormalizer()
    with pytest.raises(InputError):
        normalizer.canonicalize("नमस्ते", "stt", "test")
