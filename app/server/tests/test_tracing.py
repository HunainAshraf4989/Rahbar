import asyncio
import json
from unittest.mock import Mock

import pytest
from rahbar.contracts.models import TeachingContext
from rahbar.contracts.ports import TurnContext
from rahbar.engine.dummy import BasicNormalizer
from rahbar.engine.pipeline import Pipeline


def test_failure_records_stage_and_safe_frames_not_exception_payload(tmp_path):
    class BrokenRetrieval:
        async def retrieve(self, request, context):
            raise ValueError('private question and api-key=secret')
    async def run():
        path = tmp_path/'pipeline.jsonl'
        pipeline = Pipeline(Mock(), BrokenRetrieval(), Mock(), Mock(), BasicNormalizer(), Mock(), log_path=path)
        ctx = TurnContext('session', 'turn', 0, 'epoch', asyncio.Event())
        teaching = TeachingContext(course_id='c', content_version='v', unit_id='u', lesson_id='l', mode='tutor')
        with pytest.raises(ValueError):
            await pipeline.run_text('private learner input', teaching, ctx)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert rows[0]['event'] == 'request.started'
        assert rows[-1]['event'] == 'request.failed'
        assert rows[-1]['stage'] == 'retrieval'
        assert rows[-1]['error_type'] == 'ValueError'
        assert rows[-1]['frames'][-1]['function'] == 'retrieve'
        assert len({r['trace_id'] for r in rows}) == 1
        assert 'private' not in path.read_text() and 'secret' not in path.read_text()
    asyncio.run(run())


def test_concurrent_turns_have_independent_failure_stages_and_cancellation(tmp_path):
    class Retrieval:
        async def retrieve(self, request, context):
            await asyncio.sleep(0)
            raise RuntimeError('sensitive')
    async def run():
        path = tmp_path/'pipeline.jsonl'
        pipeline = Pipeline(Mock(), Retrieval(), Mock(), Mock(), BasicNormalizer(), Mock(), log_path=path)
        teaching = TeachingContext(course_id='c', content_version='v', unit_id='u', lesson_id='l', mode='tutor')
        a = TurnContext('s', 'a', 0, 'e', asyncio.Event())
        b = TurnContext('s', 'b', 0, 'e', asyncio.Event())
        b.cancelled.set()
        await asyncio.gather(pipeline.run_text('question', teaching, a),
                             pipeline.run_text('question', teaching, b), return_exceptions=True)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        terminal = [r for r in rows if r['event'] in {'request.failed','request.cancelled'}]
        assert {(r['turn_id'],r['stage'],r['event']) for r in terminal} == {
            ('a','retrieval','request.failed'), ('b','request','request.cancelled')}
        assert len({r['trace_id'] for r in terminal}) == 2
    asyncio.run(run())
