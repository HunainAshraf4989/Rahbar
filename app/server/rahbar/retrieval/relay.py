"""Small local relay KB for integration, not reviewed production curriculum."""

import hashlib
import re

from rahbar.contracts.models import (
    ApprovedSentence,
    CheckResult,
    EvidenceHit,
    EvidenceSet,
    EvidenceSpan,
    Passage,
    ReleaseContext,
    RetrievalRequest,
    SentenceCandidate,
    SpeechText,
    VisualTarget,
)
from rahbar.contracts.ports import TurnContext
from rahbar.engine.audio import InputError
from rahbar.engine.dummy import REFUSAL, BasicNormalizer

# Authored Urdu summaries of non-procedural corpus facts. Keep review_status=draft.
# Course2QA.json topic: Relays, solenoid, printed, circuits, circuit breaker, ...
# row 0 QA 0 (purpose); row 2 QA 3 (electromagnet); row 2 QA 2 (applications).
# These are demo facts only; no terminal numbers, ratings or wiring instructions.
SOURCE = "https://www.youtube.com/watch?v=T12PQRW2V10"
FACTS = {
    "relay-purpose": "relay ایک چھوٹے signal سے زیادہ current والے circuit کو switch کرتا ہے۔",
    "relay-magnet": "electromechanical relay ایک switch ہے جو electromagnet سے کام کرتا ہے۔",
    "relay-use": "گاڑی میں relay lighting circuit کو switch کرنے کے لیے استعمال ہوتا ہے۔",
}


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.casefold()))


class LocalRelayRetrieval:
    def __init__(self, passages: list[Passage] | None = None) -> None:
        self.lesson_passages = passages

    async def retrieve(self, request: RetrievalRequest, context: TurnContext) -> EvidenceSet:
        hits = []
        if (request.course_id == "relay-demo" and request.content_version == "relay-demo-v1"
                and request.unit_id == "relay" and (self.lesson_passages is not None
                or tokens(request.canonical_text) & {"relay", "relays", "ریلے", "ریلی", "ریلۓ"})):
            if self.lesson_passages is not None:
                return EvidenceSet(request=request, passages=[EvidenceHit(
                    passage=p, scores={"overlap": float(len(tokens(request.canonical_text)
                                                           & tokens(p.canonical_text)))},
                    method="prepared-relay-lesson") for p in self.lesson_passages])
            for passage_id, text in FACTS.items():
                score = len(tokens(request.canonical_text) & tokens(text))
                passage = Passage(
                    passage_id=passage_id, source_location=SOURCE + "#" + passage_id,
                    source_hash=hashlib.sha256(text.encode()).hexdigest(), canonical_text=text,
                    unit_ids=["relay"], track_ids=["electrical-devices"],
                    content_version="relay-demo-v1", review_status="draft", safety="ordinary",
                )
                hits.append(EvidenceHit(passage=passage, scores={"overlap": float(score)},
                                        method="local-relay-demo"))
            hits.sort(key=lambda hit: hit.scores["overlap"], reverse=True)
        return EvidenceSet(request=request, passages=hits)


class RelayChecker:
    def __init__(self, passages: list[Passage] | None = None) -> None:
        self.lesson_passages = {p.passage_id: p for p in passages} if passages is not None else None

    async def check(self, candidate: SentenceCandidate, evidence: EvidenceSet,
                    visual: VisualTarget | None, release: ReleaseContext,
                    context: TurnContext) -> CheckResult:
        refusal = (candidate.canonical_text == REFUSAL and not candidate.evidence_ids
                   and not candidate.evidence_spans)
        matches = [hit.passage for hit in evidence.passages
                   if candidate.evidence_ids == [hit.passage.passage_id]]
        supported = False
        if len(matches) == 1:
            passage = matches[0]
            if self.lesson_passages is not None:
                trusted = self.lesson_passages.get(passage.passage_id)
                supported = (
                    passage == trusted and candidate.canonical_text == passage.canonical_text
                    and candidate.evidence_spans == [EvidenceSpan(
                        passage_id=passage.passage_id, quote=passage.canonical_text)])
            else:
                supported = (
                    passage.passage_id in FACTS
                    and passage.canonical_text == candidate.canonical_text == FACTS[passage.passage_id]
                    and passage.safety == "ordinary"
                    and passage.source_hash == hashlib.sha256(passage.canonical_text.encode()).hexdigest()
                    and passage.source_location == SOURCE + "#" + passage.passage_id
                    and candidate.evidence_spans == [EvidenceSpan(
                        passage_id=passage.passage_id, quote=passage.canonical_text)])
        if not (refusal or supported) or visual is not None or candidate.visual_target is not None:
            return CheckResult(status="rejected", reasons=["Unsupported relay demo answer"],
                               evidence_checks=[])
        return CheckResult(status="refusal" if refusal else "approved",
                           reasons=["Draft relay demo; not production curriculum"],
                           evidence_checks=["Exact passage text and provenance match"],
                           output=ApprovedSentence(sentence_id=candidate.sentence_id,
                                                   canonical_text=candidate.canonical_text,
                                                   evidence_ids=candidate.evidence_ids))


class RelayNormalizer(BasicNormalizer):
    def __init__(self, passages: list[Passage] | None = None) -> None:
        self.allowed = ({p.canonical_text for p in passages} if passages is not None
                        else set(FACTS.values())) | {REFUSAL}

    def prepare_speech(self, approved: ApprovedSentence, version: str) -> SpeechText:
        if approved.canonical_text not in self.allowed:
            raise InputError("Unsupported speech text; full normalization is not installed")
        return SpeechText(sentence_id=approved.sentence_id, text=approved.canonical_text,
                          normalizer_version=version)
