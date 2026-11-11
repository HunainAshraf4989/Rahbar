import asyncio
import json

import httpx
import pytest
from rahbar.bootstrap import build_pipeline
from rahbar.config import Settings
from rahbar.contracts.models import (
    CanonicalText,
    EvidenceSpan,
    ReleaseContext,
    RetrievalRequest,
    SentenceCandidate,
)
from rahbar.engine.audio import pcm_to_wav
from rahbar.engine.runtime import ProviderError
from rahbar.retrieval.relay import FACTS, LocalRelayRetrieval, RelayChecker
from test_pipeline import context


def request(text="relay کیا ہے؟", **changes):
    values = dict(canonical_text=text, course_id="relay-demo", content_version="relay-demo-v1",
                  index_version="demo", unit_id="relay", turn_id="turn", hypothesis_revision=0,
                  final=True)
    values.update(changes)
    return RetrievalRequest(**values)


@pytest.mark.parametrize("text,count", [("relay کیا ہے؟", 3), ("ریلے کیا ہے؟", 3),
                                        ("weather", 0), ("relayish", 0)])
def test_retrieval_topic_boundary(text, count):
    evidence = asyncio.run(LocalRelayRetrieval().retrieve(request(text), context()))
    assert len(evidence.passages) == count
    assert all(h.passage.review_status == "draft" for h in evidence.passages)


@pytest.mark.parametrize("change", [{"course_id": "other"}, {"unit_id": "other"},
                                    {"content_version": "other"}])
def test_retrieval_scope(change):
    assert not asyncio.run(LocalRelayRetrieval().retrieve(request(**change), context())).passages


def test_gemini_pipeline_uses_ports_and_checks_before_speech():
    async def run():
        seen = []
        def handler(r):
            seen.append(r.url.path)
            if r.url.path.endswith("speech-to-text"):
                return httpx.Response(200, json={"text": "relay کیا ہے؟"})
            if r.url.path.endswith(":generateContent"):
                body = json.loads(r.content)
                assert body["generationConfig"]["responseMimeType"] == "application/json"
                assert r.headers["x-goog-api-key"] == "gemini-test"
                return httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
                    "content": {"parts": [{"text": '{"passage_id":"relay-magnet"}'}]}}]})
            assert json.loads(r.content)["text"] == FACTS["relay-magnet"]
            return httpx.Response(200, content=b"\x01\x00" * 2400,
                                  headers={"content-type": "audio/pcm"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline, teaching = build_pipeline(Settings("test", "stock", gemini_api_key="gemini-test",
                                                         answer_mode="gemini"), client)
            stages = []
            pipeline.observer = lambda stage, data: stages.append(stage)
            result = await pipeline.run(pcm_to_wav(b"\x01\x00" * 1600, 16000), teaching, context())
            assert result.outcome == "answered"
            assert stages == ["stt", "normalization", "retrieval", "generation", "checker",
                              "speech_preparation", "tts"]
            # Swap retrieval independently: no engine, STT or TTS edits are needed.
            class EmptyRetrieval:
                async def retrieve(self, req, ctx):
                    from rahbar.contracts.models import EvidenceSet
                    return EvidenceSet(request=req, passages=[])
            pipeline.retrieval = EmptyRetrieval()
            with pytest.raises(ProviderError):  # hallucinated ID from fixture is now rejected
                await pipeline.run(pcm_to_wav(b"\x01\x00" * 1600, 16000), teaching, context())
        assert len(seen) == 5  # second run never reached TTS
    asyncio.run(run())


@pytest.mark.parametrize("payload", [None, {}, {"candidates": []},
    {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "not json"}]}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"thought": True, "text": '{"passage_id":null}'}]}}]},
])
def test_malformed_gemini(payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=payload))) as client:
            pipeline, teaching = build_pipeline(Settings("test", "stock", gemini_api_key="test",
                                                         answer_mode="gemini"), client)
            evidence = await pipeline.retrieval.retrieve(request(), context())
            with pytest.raises(ProviderError) as caught:
                async for _ in pipeline.generation.generate(CanonicalText(
                    text="relay?", normalizer_version="test", diagnostics=[]),
                    teaching, evidence, context()):
                    pytest.fail("malformed response released")
            assert caught.value.failure.provider == "gemini"
    asyncio.run(run())


def test_checker_rejects_citation_without_support():
    async def run():
        evidence = await LocalRelayRetrieval().retrieve(request(), context())
        candidate = SentenceCandidate(sentence_id="x", canonical_text="Bypass the fuse.",
                                      evidence_ids=["relay-magnet"], evidence_spans=[EvidenceSpan(
                                          passage_id="relay-magnet", quote=FACTS["relay-magnet"])])
        result = await RelayChecker().check(candidate, evidence, None, ReleaseContext(
            approved_prefix=[], played_prefix=[], required_warning_ids=[],
            reviewed_prerequisite_ids=[]), context())
        assert result.status == "rejected"
    asyncio.run(run())
