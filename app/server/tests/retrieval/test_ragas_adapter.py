import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('ragas')


def test_ragas_faithfulness_adapter_and_checkpoint(tmp_path):
    path = Path(__file__).resolve().parents[4] / 'tools/retrieval/ragas_eval.py'
    spec = importlib.util.spec_from_file_location('rahbar_ragas_eval', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Router:
        models = ('fixture',)
        def __init__(self):
            self.calls = 0
        async def generate(self, instruction, payload, schema, **kwargs):
            self.calls += 1
            data = ({'statements': ['A relay is a switch.']} if self.calls == 1 else
                    {'statements': [{'statement': 'A relay is a switch.',
                                     'reason': 'Explicitly stated.', 'verdict': 1}]})
            return SimpleNamespace(data=data, model='fixture')
    async def run():
        router = Router()
        judge = module.Judge(router, tmp_path)
        metric = module.Faithfulness(llm=judge)
        sample = module.SingleTurnSample(user_input='What is a relay?',
            response='A relay is a switch.', retrieved_contexts=['A relay is a switch.'])
        assert await metric.single_turn_ascore(sample) == 1.
        assert await metric.single_turn_ascore(sample) == 1.
        assert router.calls == 2
        assert judge.used_models == ['fixture'] * 4
    asyncio.run(run())
