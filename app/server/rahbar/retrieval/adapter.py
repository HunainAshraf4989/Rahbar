"""Channel-neutral retrieval port backed by a fixed hybrid index release."""
import asyncio
from typing import Any, Protocol

from rahbar.contracts.models import EvidenceHit, EvidenceSet, Passage, RetrievalRequest
from rahbar.contracts.ports import TurnContext
from rahbar.engine.runtime import check_cancelled


class Index(Protocol):
    collection: str
    def search(self, question: str, k: int = 10, *, unit_id: str | None = None,
               reviewed_only: bool = False, mode: str = 'hybrid') -> list[dict[str, Any]]: ...


class HybridRetrieval:
    def __init__(self, index: Index, course_id: str, content_version: str):
        self.index, self.course_id, self.content_version = index, course_id, content_version

    async def retrieve(self, request: RetrievalRequest, context: TurnContext) -> EvidenceSet:
        check_cancelled(context)
        if (request.turn_id != context.turn_id or request.course_id != self.course_id
                or request.content_version != self.content_version
                or request.index_version != self.index.collection):
            raise ValueError('Retrieval identity or release mismatch')
        global_hits = await asyncio.to_thread(self.index.search, request.canonical_text, 10,
                                              reviewed_only=True)
        rankings = [global_hits]
        if request.mode == 'lesson_first':
            rankings.insert(0, await asyncio.to_thread(self.index.search, request.canonical_text, 10,
                                                       unit_id=request.unit_id, reviewed_only=True))
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
            if record.get('review_status') != 'reviewed' or not record.get('provenance'):
                continue
            source = record['provenance'][0]
            passage = Passage(passage_id=record['passage_id'],
                              source_location=source['location'], source_hash=source['source_hash'],
                              canonical_text=record['canonical_text'], unit_ids=record['unit_ids'],
                              track_ids=record.get('track_ids', []), content_version=self.content_version,
                              review_status='reviewed', reviewed_by=record.get('reviewed_by'),
                              review_reference=record.get('review_reference'), safety=record['safety'])
            hits.append(EvidenceHit(passage=passage, scores={'rrf': record['rrf_score']},
                                    method='bge-m3+bm25+rrf'))
        return EvidenceSet(request=request, passages=hits)
