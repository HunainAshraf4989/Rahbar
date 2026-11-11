"""Synthetic integration content, never represented as reviewed curriculum."""

import hashlib
import re
from collections.abc import AsyncIterator

from rahbar.contracts.models import (
    ApprovedSentence,
    CanonicalText,
    CheckResult,
    EvidenceHit,
    EvidenceSet,
    EvidenceSpan,
    Passage,
    ReleaseContext,
    RetrievalRequest,
    SentenceCandidate,
    SpeechText,
    TeachingContext,
    VisualTarget,
)
from rahbar.contracts.ports import TurnContext
from rahbar.engine.audio import InputError

ANSWER = "یہ pipeline کا آزمائشی جواب ہے۔"
REFUSAL = "یہ آزمائشی نظام اس سوال کا جواب نہیں دے سکتا۔ اپنے instructor سے رجوع کریں۔"
NORMALIZER_VERSION = "dummy-safe-v1"


class BasicNormalizer:
    def canonicalize(self, text: str, boundary: str, version: str) -> CanonicalText:
        text = " ".join(text.split())
        if not text or re.search(r"[\u0900-\u097f]", text):
            raise InputError("Empty transcript or unsupported script; repeat in Urdu or English")
        return CanonicalText(text=text, normalizer_version=version,
                             diagnostics=[f"{boundary}: linguistic validation not implemented"])

    def prepare_speech(self, approved: ApprovedSentence, version: str) -> SpeechText:
        # Only these authored strings are supported until the full S2 normalizer lands.
        if approved.canonical_text not in {ANSWER, REFUSAL}:
            raise InputError("Speech preparation does not support this sentence")
        return SpeechText(sentence_id=approved.sentence_id, text=approved.canonical_text,
                          normalizer_version=version)


class DummyRetrieval:
    async def retrieve(self, request: RetrievalRequest, context: TurnContext) -> EvidenceSet:
        query = request.canonical_text.casefold().strip(" .?!؟۔")
        hits = []
        if query in {"pipeline test", "آزمائشی سوال"}:
            hits = [EvidenceHit(passage=Passage(
                passage_id="dummy-pipeline", source_location="builtin:dummy-pipeline-v1",
                source_hash=hashlib.sha256(ANSWER.encode()).hexdigest(), canonical_text=ANSWER,
                unit_ids=[request.unit_id], track_ids=["dummy"],
                content_version=request.content_version, review_status="draft", safety="ordinary",
            ), scores={"exact": 1.0}, method="synthetic-exact-match")]
        return EvidenceSet(request=request, passages=hits)


class DeterministicGeneration:
    async def generate(self, question: CanonicalText, teaching: TeachingContext,
                       evidence: EvidenceSet, context: TurnContext) -> AsyncIterator[SentenceCandidate]:
        if evidence.passages:
            passage = evidence.passages[0].passage
            yield SentenceCandidate(sentence_id="reply", canonical_text=passage.canonical_text,
                                    evidence_ids=[passage.passage_id], evidence_spans=[
                                        EvidenceSpan(passage_id=passage.passage_id,
                                                     quote=passage.canonical_text)])
        else:
            yield SentenceCandidate(sentence_id="reply", canonical_text=REFUSAL,
                                    evidence_ids=[], evidence_spans=[])


class DummyChecker:
    async def check(self, candidate: SentenceCandidate, evidence: EvidenceSet,
                    visual: VisualTarget | None, release: ReleaseContext,
                    context: TurnContext) -> CheckResult:
        refusal = (candidate.canonical_text == REFUSAL and not candidate.evidence_ids
                   and not candidate.evidence_spans)
        supported = (
            candidate.canonical_text == ANSWER and candidate.evidence_ids == ["dummy-pipeline"]
            and candidate.evidence_spans == [EvidenceSpan(passage_id="dummy-pipeline", quote=ANSWER)]
            and any(h.passage.passage_id == "dummy-pipeline"
                    and h.passage.canonical_text == ANSWER
                    and h.passage.source_location == "builtin:dummy-pipeline-v1"
                    and h.passage.source_hash == hashlib.sha256(ANSWER.encode()).hexdigest()
                    and h.passage.safety == "ordinary" for h in evidence.passages)
        )
        if not (refusal or supported) or visual is not None or candidate.visual_target is not None:
            return CheckResult(status="rejected", reasons=["Outside synthetic allowlist"],
                               evidence_checks=[])
        return CheckResult(
            status="refusal" if refusal else "approved", reasons=["Synthetic integration only"],
            evidence_checks=["Exact authored text; no curriculum grounding claim"],
            output=ApprovedSentence(sentence_id=candidate.sentence_id,
                                    canonical_text=candidate.canonical_text,
                                    evidence_ids=candidate.evidence_ids),
        )
