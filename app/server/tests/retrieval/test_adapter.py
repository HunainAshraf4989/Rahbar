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
