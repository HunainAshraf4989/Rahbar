import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from rahbar.config import Settings
from rahbar.engine.audio import InputError
from rahbar.engine.bake import bake_lesson, cached_pcm
from rahbar.engine.lesson import relay_asset, relay_content, relay_lesson, relay_passages
from rahbar.providers.elevenlabs import ElevenLabs
from test_pipeline import context

ROOT = Path(__file__).resolve().parents[3]


def test_lesson_passages_and_visuals():
    content = relay_content(ROOT)
    asset = relay_asset(ROOT)
    lesson = relay_lesson(asset, ROOT)
    assert len(lesson.sentences) == 7
    assert asset.review_status == content.review_status == "draft"
    for entry, sentence, passage in zip(content.entries, lesson.sentences, relay_passages(ROOT)):
        assert sentence.evidence_ids == [passage.passage_id]
        assert entry.text in sentence.canonical_text
        assert asset.permits(sentence.visual_target)


@pytest.mark.skipif(not (ROOT / "education_data/transcriptfolder/Course2transcriptions.json").exists(),
                    reason="Local-only education corpus is not distributed with the repository")
def test_local_corpus_provenance():
    content = relay_content(ROOT)
    for entry in content.entries:
        source_path = ROOT / entry.source.file
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == content.source_file_sha256
        row = json.loads(source_path.read_text())[entry.source.topic][entry.source.row]
        assert entry.source.quote in row["transcript"]
        assert row["url"] == entry.source.url
        assert hashlib.sha256(row["transcript"].encode()).hexdigest() == entry.source.transcript_sha256


def test_bake_reuses_and_invalidates_audio(tmp_path):
    async def run():
        calls = []
        def handle(request):
            calls.append(json.loads(request.content)["text"])
            return httpx.Response(200, content=b"\x01\x00" * 2400,
                                  headers={"content-type": "audio/pcm"})
        settings = Settings("secret", "voice")
        lesson = relay_lesson(relay_asset(ROOT), ROOT)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            speech = ElevenLabs(settings, client)
            baked = await bake_lesson(lesson, speech, settings, tmp_path, context())
            assert len(calls) == 7
            assert all(s.audio is not None for s in baked.sentences)
            assert await bake_lesson(lesson, speech, settings, tmp_path, context()) == baked
            assert len(calls) == 7
            path = tmp_path / Path(baked.sentences[0].audio.path).name
            assert len(cached_pcm(path)) == 4800
            path.write_bytes(path.read_bytes()[:-2])
            await bake_lesson(lesson, speech, settings, tmp_path, context())
            assert len(calls) == 8  # only the corrupt clip is regenerated
            changed = Settings("secret", "differentvoice")
            await bake_lesson(lesson, ElevenLabs(changed, client), changed, tmp_path, context())
            assert len(calls) == 15
            changed_sentence = lesson.sentences[0].model_copy(update={
                "canonical_text": lesson.sentences[0].canonical_text + " دوبارہ سنیں۔"})
            changed_lesson = lesson.model_copy(update={
                "sentences": [changed_sentence, *lesson.sentences[1:]]})
            await bake_lesson(changed_lesson, speech, settings, tmp_path, context())
            assert len(calls) == 16
    asyncio.run(run())


def test_cancelled_bake_never_calls_provider(tmp_path):
    async def run():
        def handle(request):
            pytest.fail("cancelled bake reached provider")
        settings = Settings("secret", "voice")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            ctx = context()
            ctx.cancelled.set()
            with pytest.raises(asyncio.CancelledError):
                await bake_lesson(relay_lesson(relay_asset(ROOT), ROOT),
                                  ElevenLabs(settings, client), settings, tmp_path, ctx)
        assert not (tmp_path / "lesson.json").exists()
    asyncio.run(run())


def test_unavailable_part_is_rejected():
    asset = relay_asset(ROOT)
    asset = asset.model_copy(update={"views": [asset.views[0]]})
    with pytest.raises(InputError):
        relay_lesson(asset, ROOT)
