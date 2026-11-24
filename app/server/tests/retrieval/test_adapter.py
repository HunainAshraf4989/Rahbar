import asyncio

import pytest
from rahbar.contracts.models import RetrievalRequest
from rahbar.contracts.ports import TurnContext
from rahbar.retrieval.adapter import HybridRetrieval


class Index:
    collection='index-v1'
    def search(self,*args,**kwargs):
        return []


def test_adapter_identity_and_cancellation():
    async def run():
        context=TurnContext('session','turn',0,'epoch',asyncio.Event())
        request=RetrievalRequest(canonical_text='relay',course_id='course',content_version='v1',
                                 index_version='index-v1',unit_id='relay',turn_id='turn',
                                 hypothesis_revision=0,final=True)
        adapter=HybridRetrieval(Index(),'course','v1')
        assert (await adapter.retrieve(request,context)).passages==[]
        with pytest.raises(ValueError):
            await adapter.retrieve(request.model_copy(update={'content_version':'other'}),context)
        context.cancelled.set()
        with pytest.raises(asyncio.CancelledError):
            await adapter.retrieve(request,context)
    asyncio.run(run())


def test_cancellation_returns_before_blocking_search_and_starts_no_second_search():
    import threading
    async def run():
        entered, release = threading.Event(), threading.Event()
        calls = []
        class SlowIndex:
            collection = 'index-v1'
            def search(self, *args, **kwargs):
                calls.append(kwargs)
                entered.set()
                assert release.wait(2)
                return []
        context = TurnContext('s', 't', 0, 'e', asyncio.Event())
        request = RetrievalRequest(canonical_text='question', course_id='c', content_version='v',
            index_version='index-v1', unit_id='u', turn_id='t', hypothesis_revision=3, final=True)
        task = asyncio.create_task(HybridRetrieval(SlowIndex(), 'c', 'v').retrieve(request, context))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            context.cancelled.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, .5)
            assert len(calls) == 1
        finally:
            release.set()
    asyncio.run(run())


def test_pipeline_uses_bound_release_and_checks_evidence_before_speech():
    import base64
    import hashlib
    from unittest.mock import Mock

    from rahbar.contracts.events import AudioChunk
    from rahbar.contracts.models import ProviderMetadata, TeachingContext
    from rahbar.contracts.ports import SynthesisChunk
    from rahbar.engine.dummy import ANSWER, BasicNormalizer, DeterministicGeneration, DummyChecker
    from rahbar.engine.pipeline import Pipeline

    record = dict(passage_id='dummy-pipeline', canonical_text=ANSWER, unit_ids=['u'],
        provenance=[dict(location='builtin:dummy-pipeline-v1', source_hash=hashlib.sha256(ANSWER.encode()).hexdigest())],
        review_status='reviewed', publication_eligible=True, reviewed_by='fixture',
        review_reference='synthetic-test-only', safety='ordinary', rrf_score=1.)
    class FixtureIndex:
        collection = 'release-fixture'
        def search(self, question, k, **kwargs):
            assert question == 'pipeline test' and kwargs['reviewed_only']
            return [record]
    class Speech:
        calls = 0
        async def synthesize(self, speech, context):
            self.calls += 1
            yield SynthesisChunk(audio=AudioChunk(stream_id='t', chunk_sequence=0,
                sentence_id=speech.sentence_id, data=base64.b64encode(b'\0\0'*240).decode()),
                metadata=ProviderMetadata(provider='fixture', model='fixture', mock=True), sample_rate=24000)
    async def run():
        speech, events = Speech(), []
        retrieval = HybridRetrieval(FixtureIndex(), 'c', 'v', reviewed_only=True)
        pipeline = Pipeline(Mock(), retrieval, DeterministicGeneration(), DummyChecker(),
            BasicNormalizer(), speech, observer=lambda stage,data: events.append((stage,data)),
            index_version=retrieval.index_version)
        context = TurnContext('s','t',0,'e',asyncio.Event())
        teaching = TeachingContext(course_id='c', content_version='v', unit_id='u', lesson_id='l', mode='tutor')
        result = await pipeline.run_text('  pipeline   test ', teaching, context)
        assert result.outcome == 'answered' and speech.calls == 1
        evidence = next(data for stage,data in events if stage=='retrieval')
        assert evidence['request']['index_version'] == 'release-fixture'
        assert [stage for stage,_ in events].index('checker') < [stage for stage,_ in events].index('tts')
        record['content_version'] = 'foreign-release'
        with pytest.raises(ValueError, match='release mismatch'):
            await pipeline.run_text('pipeline test', teaching, context)
        assert speech.calls == 1
        record.pop('content_version')
        record['publication_eligible'] = False
        result = await pipeline.run_text('pipeline test', teaching, context)
        assert result.outcome == 'refused'
    asyncio.run(run())


def test_cancelled_retrieval_result_never_reaches_generation():
    from unittest.mock import Mock

    from rahbar.contracts.models import EvidenceSet, TeachingContext
    from rahbar.engine.dummy import BasicNormalizer
    from rahbar.engine.pipeline import Pipeline
    class CancelAtReturn:
        async def retrieve(self, request, context):
            context.cancelled.set()
            return EvidenceSet(request=request, passages=[])
    async def run():
        generation, speech = Mock(), Mock()
        pipeline = Pipeline(Mock(), CancelAtReturn(), generation, Mock(), BasicNormalizer(), speech)
        teaching = TeachingContext(course_id='c', content_version='v', unit_id='u', lesson_id='l', mode='tutor')
        context = TurnContext('s','t',0,'e',asyncio.Event())
        with pytest.raises(asyncio.CancelledError):
            await pipeline.run_text('question', teaching, context)
        generation.generate.assert_not_called()
        speech.synthesize.assert_not_called()
    asyncio.run(run())


def test_supplied_adapter_preserves_draft_status_without_reviewer_metadata():
    record = dict(passage_id='source-1', canonical_text='A relay is a switch.', unit_ids=['u'],
        provenance=[dict(location='supplied:page-1', source_hash='source-hash')],
        review_status='draft', publication_eligible=False, safety='ordinary', rrf_score=1.)
    class SuppliedIndex:
        collection = 'v'
        def search(self, *args, **kwargs):
            assert not kwargs['reviewed_only']
            return [record]
    async def run():
        context = TurnContext('s','t',0,'e',asyncio.Event())
        request = RetrievalRequest(canonical_text='relay',course_id='c',content_version='v',
            index_version='v',unit_id='u',turn_id='t',hypothesis_revision=0,final=True)
        result = await HybridRetrieval(SuppliedIndex(),'c','v').retrieve(request,context)
        assert len(result.passages) == 1
        assert result.passages[0].passage.review_status == 'draft'
        assert result.passages[0].passage.reviewed_by is None
    asyncio.run(run())
