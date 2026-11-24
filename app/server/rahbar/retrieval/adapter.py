"""Channel-neutral retrieval port backed by a fixed hybrid index release."""
import asyncio
from typing import Any, Protocol

from rahbar.contracts.models import EvidenceHit, EvidenceSet, Passage, RetrievalRequest
from rahbar.contracts.ports import TurnContext
from rahbar.engine.runtime import check_cancelled
from rahbar.retrieval.content_policy import supplied_content_eligible


class Index(Protocol):
    collection: str
    def search(self, question: str, k: int = 10, *, unit_id: str | None = None,
               reviewed_only: bool = False, mode: str = 'hybrid') -> list[dict[str, Any]]: ...


class HybridRetrieval:
    def __init__(self, index: Index, course_id: str, content_version: str, *, reviewed_only: bool = False):
        self.index, self.course_id, self.content_version = index, course_id, content_version
        self.reviewed_only = reviewed_only

    @property
    def index_version(self) -> str:
        return self.index.collection

    async def _search(self, question: str, context: TurnContext,
                      unit_id: str | None = None) -> list[dict[str, Any]]:
        check_cancelled(context)
        work = asyncio.create_task(asyncio.to_thread(self.index.search, question, 10,
                                                    unit_id=unit_id, reviewed_only=self.reviewed_only))
        cancelled = asyncio.create_task(context.cancelled.wait())
        try:
            await asyncio.wait({work, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            check_cancelled(context)
            return await work
        finally:
            # A blocking worker may finish later; its results are never released.
            work.cancel()
            cancelled.cancel()
            await asyncio.gather(work, cancelled, return_exceptions=True)

    async def retrieve(self, request: RetrievalRequest, context: TurnContext) -> EvidenceSet:
        check_cancelled(context)
        if (request.turn_id != context.turn_id or request.course_id != self.course_id
                or request.content_version != self.content_version
                or request.index_version != self.index.collection):
            raise ValueError('Retrieval identity or release mismatch')
        global_hits = await self._search(request.canonical_text, context)
        rankings = [global_hits]
        if request.mode == 'lesson_first':
            rankings.insert(0, await self._search(request.canonical_text, context, request.unit_id))
        check_cancelled(context)
        # Lesson relevance cannot eliminate course-wide evidence for cross-topic questions.
        selected: dict[str, dict[str, Any]] = {}
        for rank in range(10):
            for ranking in rankings:
                if rank < len(ranking):
                    hit = ranking[rank]
                    selected.setdefault(hit['passage_id'], hit)
        hits = []
        for record in list(selected.values())[:10]:
            if not supplied_content_eligible(record):
                continue
            if self.reviewed_only and (record.get('review_status') != 'reviewed' or not record.get('provenance')
                    or not record.get('publication_eligible') or not record.get('reviewed_by')
                    or not record.get('review_reference') or not record.get('unit_ids')):
                continue
            if record.get('content_version', self.content_version) != self.content_version:
                raise ValueError('Passage release mismatch')
            source = record['provenance'][0]
            passage = Passage(passage_id=record['passage_id'],
                              source_location=source['location'], source_hash=source['source_hash'],
                              canonical_text=record['canonical_text'], unit_ids=record['unit_ids'],
                              track_ids=record.get('track_ids', []), content_version=self.content_version,
                              review_status=record.get('review_status', 'draft'), reviewed_by=record.get('reviewed_by'),
                              review_reference=record.get('review_reference'), safety=record['safety'])
            hits.append(EvidenceHit(passage=passage, scores={'rrf': record['rrf_score']},
                                    method='bge-m3+bm25+rrf'))
        return EvidenceSet(request=request, passages=hits)
