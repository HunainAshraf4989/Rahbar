"""Provider failures, conversational context and media conversion tests."""

import asyncio
import io
import json
import wave
from types import SimpleNamespace

import httpx
import pytest
from rahbar.config import Settings
from rahbar.contracts.models import TeachingContext
from rahbar.whatsapp.audio import AudioCodec
from rahbar.whatsapp.config import WhatsAppSettings
from rahbar.whatsapp.meta import AmbiguousSend, ChannelError, MetaClient, log_rejection
from rahbar.whatsapp.tutor import WhatsAppTutor


def test_context_resolver_receives_five_pairs_and_explicit_quote():
    async def scenario():
        requests = []
        seen = []
        history = [{"question": f"Question {i}", "answer": f"Answer {i}"} for i in range(7)]

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {"question": "What does the relay coil do?"}
                                        )
                                    }
                                ]
                            },
                        }
                    ]
                },
            )

        class Pipeline:
            async def run_text(self, question, teaching, context):
                seen.append(question)
                return SimpleNamespace(
                    sentences=[SimpleNamespace(canonical_text="Approved answer")],
                    wav=b"fixture-wave",
                )

        settings = Settings("fixture", "voice", gemini_api_key="fixture", answer_mode="gemini")
        teaching = TeachingContext(
            course_id="relay-demo",
            content_version="relay-demo-v1",
            unit_id="relay",
            lesson_id="relay-demo",
            mode="tutor",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tutor = WhatsAppTutor(Pipeline(), teaching, settings, client)
            assert await tutor.answer("What does it do?", history, history[3]) == (
                "Approved answer",
                b"fixture-wave",
            )
        body = json.loads(requests[0]["contents"][0]["parts"][0]["text"])
        assert body["history"] == history[-5:]
        assert body["quoted_pair"] == history[3]
        assert seen == ["What does the relay coil do?"]

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_send_distinguishes_rejection_from_uncertain_delivery(status):
    async def scenario():
        settings = WhatsAppSettings("12345678", "87654321", "token", "secret", "verify")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, json={"error": "private response"})
            )
        ) as client:
            expected = AmbiguousSend if status == 500 else ChannelError
            with pytest.raises(expected) as error:
                await MetaClient(settings, client).send("923001234567", "wamid.q", text="Hello")
            assert "private response" not in str(error.value)

    asyncio.run(scenario())


def test_rejection_logs_only_numeric_codes(caplog):
    response = httpx.Response(
        400,
        json={
            "error": {
                "code": 131030,
                "error_subcode": 7,
                "message": "private provider body",
                "error_data": {"details": "private phone"},
            }
        },
    )
    log_rejection(response, "send")
    assert "code=131030 subcode=7" in caplog.text
    assert "private" not in caplog.text


def test_transport_timeout_is_not_silently_retried():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("fixture")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            meta = MetaClient(
                WhatsAppSettings("12345678", "87654321", "token", "secret", "verify"), client
            )
            with pytest.raises(AmbiguousSend):
                await meta.send("923001234567", "wamid.q", text="Hello")
        assert len(calls) == 1

    asyncio.run(scenario())


def test_media_url_cannot_send_token_to_untrusted_host():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "url": "https://attacker.example/audio.ogg",
                    "file_size": 128,
                    "mime_type": "audio/ogg",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            meta = MetaClient(
                WhatsAppSettings("12345678", "87654321", "token", "secret", "verify"), client
            )
            with pytest.raises(ChannelError, match="host"):
                await meta.download("12345678")
        assert len(calls) == 1

    asyncio.run(scenario())


def test_real_ogg_opus_conversion_round_trip():
    import imageio_ffmpeg
    from rahbar.engine.audio import validate_wav

    async def scenario():
        data = io.BytesIO()
        with wave.open(data, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\0\0" * 2400)
        codec = AudioCodec(imageio_ffmpeg.get_ffmpeg_exe())
        ogg = await codec.outgoing(data.getvalue())
        assert ogg.startswith(b"OggS")
        wav = await codec.incoming(ogg, "audio/ogg")
        validate_wav(wav)
        with wave.open(io.BytesIO(wav)) as decoded:
            assert decoded.getframerate() == 16000
            assert decoded.getnchannels() == 1

    asyncio.run(scenario())
