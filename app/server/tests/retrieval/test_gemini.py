import asyncio

import httpx
import pytest
from rahbar.retrieval.gemini import GeminiRouter, QuotaExhausted


def test_quota_fallback_is_bounded_and_recorded():
    called = []
    def handle(request):
        called.append(request.url.path)
        if 'first:' in request.url.path:
            return httpx.Response(429, headers={'retry-after': '120'})
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP',
            'content': {'parts': [{'text': '{"ok":true}'}]}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            router = GeminiRouter(client, 'test-secret', ['first', 'second'])
            result = await router.generate('instruction', {}, {})
            assert result.model == 'second'
            assert result.attempted == ('first', 'second')
            await router.generate('instruction', {}, {})
    asyncio.run(run())
    assert len(called) == 3


def test_all_exhausted_stops():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(429))) as client:
            with pytest.raises(QuotaExhausted):
                await GeminiRouter(client, 'secret', ['a', 'b']).generate('', {}, {})
    asyncio.run(run())


def test_unavailable_model_falls_back_without_retry_loop():
    async def run():
        def handle(request):
            if 'retired:' in request.url.path:
                return httpx.Response(404)
            return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{}'}]}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            router=GeminiRouter(client,'secret',['retired','available'])
            assert (await router.generate('',{},{})).model=='available'
            assert (await router.generate('',{},{})).attempted==('available',)
    asyncio.run(run())


def test_json_mode_does_not_send_empty_schema():
    import json
    async def run():
        def handle(request):
            config = json.loads(request.content)['generationConfig']
            assert config['responseMimeType'] == 'application/json'
            assert 'responseJsonSchema' not in config
            return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP',
                'content': {'parts': [{'text': '{"statements":["A relay switches a circuit."]}'}]}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await GeminiRouter(client, 'secret', ['available']).generate('', {}, None)
            assert result.data['statements']
    asyncio.run(run())


@pytest.mark.parametrize('model', ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'other-model'])
def test_flash_thinking_is_bounded_and_truncated_output_is_rejected(model):
    import json
    async def run():
        def handle(request):
            config = json.loads(request.content)['generationConfig']
            if model.startswith('gemini-2.5-flash'):
                assert config['thinkingConfig'] == {'thinkingBudget': 512}
            else:
                assert 'thinkingConfig' not in config
            return httpx.Response(200, json={'candidates': [{'finishReason': 'MAX_TOKENS',
                'content': {'parts': [{'text': '{"outcome":"answered"}'}]}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(RuntimeError, match='Invalid Gemini structured response'):
                await GeminiRouter(client, 'secret', [model]).generate('', {}, None)
    asyncio.run(run())
