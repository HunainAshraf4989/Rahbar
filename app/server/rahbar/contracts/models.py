"""Version-one contracts. Structural validity never establishes authorization or grounding."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(min_length=1, pattern=r"\S")]
Count = Annotated[int, Field(ge=0)]
Text = Annotated[str, Field(min_length=1)]
Outcome = Literal["passed", "not_yet_passed", "incomplete"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class VisualTarget(Model):
    asset_id: ID
    asset_version: ID
    view_id: ID
    frame_id: ID | None = None
    part_id: ID


class Part(Model):
    part_id: ID
    label_en: Text
    label_ur: Text
    spoken_ur: Text


class AssetView(Model):
    view_id: ID
    frame_id: ID | None = None
    image: Text
    overlay: Text
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    visible_parts: list[ID]


class AssetManifest(Model):
    asset_id: ID
    asset_version: ID
    variant: Text
    sources: list[Text] = Field(min_length=1)
    review_status: Literal["draft", "reviewed"]
    parts: list[Part] = Field(min_length=1)
    views: list[AssetView] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_parts_and_views(self) -> AssetManifest:
        parts = [p.part_id for p in self.parts]
        keys = [(v.view_id, v.frame_id) for v in self.views]
        if len(set(parts)) != len(parts) or len(set(keys)) != len(keys):
            raise ValueError("duplicate part or view/frame")
        for view in self.views:
            if len(set(view.visible_parts)) != len(view.visible_parts):
                raise ValueError("duplicate visibility entry")
            if not set(view.visible_parts) <= set(parts):
                raise ValueError("unknown visible part")
        return self

    def permits(self, target: VisualTarget) -> bool:
        return (
            target.asset_id == self.asset_id
            and target.asset_version == self.asset_version
            and any(
                v.view_id == target.view_id
                and v.frame_id == target.frame_id
                and target.part_id in v.visible_parts
                for v in self.views
            )
        )


class SourceMapping(Model):
    source_revision: ID
    source_file: Text
    course: ID
    semester: ID | None = None
    stream: ID
    leaf_id: ID
    original_key: Text
    aliases: list[str] = Field(default_factory=list)
    unit_ids: list[ID]


class CurriculumUnit(Model):
    unit_id: ID
    title_en: Text
    title_ur: Text
    family_id: ID
    track_ids: list[ID] = Field(min_length=1)
    sources: list[SourceMapping] = Field(min_length=1)
    outcomes: list[Text]
    content_version: ID
    readiness: Literal["draft", "mapped", "playable"]


class Passage(Model):
    passage_id: ID
    source_location: Text
    source_hash: Text
    canonical_text: Text
    unit_ids: list[ID]
    track_ids: list[ID]
    content_version: ID
    review_status: Literal["draft", "reviewed", "withheld"]
    safety: Literal["ordinary", "safety", "procedure"]
    reviewed_by: ID | None = None
    review_reference: Text | None = None
    exclusion_reason: Text | None = None

    @model_validator(mode="after")
    def review_evidence(self) -> Passage:
        if self.review_status == "reviewed" and not (self.reviewed_by and self.review_reference):
            raise ValueError("reviewed passages require review provenance")
        if self.review_status == "withheld" and not self.exclusion_reason:
            raise ValueError("withheld passages require a reason")
        return self


class EvidenceHit(Model):
    passage: Passage
    scores: dict[str, float]
    method: Text


class RetrievalRequest(Model):
    canonical_text: Text
    course_id: ID
    content_version: ID
    index_version: ID
    unit_id: ID
    turn_id: ID
    hypothesis_revision: Count
    final: bool
    mode: Literal["lesson_first", "whole_course"] = "lesson_first"


class EvidenceSet(Model):
    request: RetrievalRequest
    passages: list[EvidenceHit]

    @model_validator(mode="after")
    def version_matches(self) -> EvidenceSet:
        ids = [h.passage.passage_id for h in self.passages]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence")
        if any(h.passage.content_version != self.request.content_version for h in self.passages):
            raise ValueError("mixed evidence versions")
        return self


class EvidenceSpan(Model):
    passage_id: ID
    quote: Text


class SentenceCandidate(Model):
    sentence_id: ID
    canonical_text: Text
    evidence_ids: list[ID]
    evidence_spans: list[EvidenceSpan]
    visual_target: VisualTarget | None = None


class ApprovedSentence(Model):
    sentence_id: ID
    canonical_text: Text
    evidence_ids: list[ID]
    visual_target: VisualTarget | None = None


class CheckResult(Model):
    status: Literal["approved", "rejected", "refusal"]
    reasons: list[Text]
    evidence_checks: list[Text]
    output: ApprovedSentence | None = None

    @model_validator(mode="after")
    def output_matches_status(self) -> CheckResult:
        if (self.status == "rejected") != (self.output is None):
            raise ValueError("rejected output must be absent; approved/refusal output is required")
        return self


class ReleaseContext(Model):
    approved_prefix: list[ID]
    played_prefix: list[ID]
    procedure_id: ID | None = None
    required_warning_ids: list[ID]
    reviewed_prerequisite_ids: list[ID]


class ProviderMetadata(Model):
    provider: ID
    model: ID
    mock: bool
    timings_ms: dict[str, float] = Field(default_factory=dict)
    language: str | None = None
    converter_version: str | None = None
    decode: dict[str, str | int | float | bool] = Field(default_factory=dict)


class TranscriptUpdate(Model):
    turn_id: ID
    hypothesis_revision: Count
    text: str
    final: bool
    metadata: ProviderMetadata
    confidence: float | None = None


class CanonicalText(Model):
    text: Text
    normalizer_version: ID
    diagnostics: list[str]


class SpeechText(Model):
    sentence_id: ID
    text: Text
    normalizer_version: ID


class AudioFormat(Model):
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: Annotated[int, Field(gt=0)]
    channels: Literal[1] = 1


class Capabilities(Model):
    protocol: Literal["rahbar.v1"] = "rahbar.v1"
    input_audio: AudioFormat
    max_utterance_ms: Annotated[int, Field(gt=0)]
    max_queue_chunks: Annotated[int, Field(gt=0)]
    stt_streaming: bool
    visuals: bool
    profile: Literal["mock", "real", "evaluation"]

    @model_validator(mode="after")
    def input_rate(self) -> Capabilities:
        if self.input_audio.sample_rate != 16000:
            raise ValueError("v1 input is 16 kHz PCM16 mono")
        return self


class LessonCursor(Model):
    cursor_id: ID
    revision: Count
    unit_id: ID
    content_version: ID
    lesson_id: ID
    lesson_version: ID
    last_completed_sentence_id: ID | None = None
    restart_sentence_id: ID | None = None


class TeachingContext(Model):
    course_id: ID
    content_version: ID
    unit_id: ID
    lesson_id: ID
    mode: Literal["tutor", "viva"]
    confirmed_sentence_id: ID | None = None
    visual_context: VisualTarget | None = None


class SessionCreate(Model):
    """Untrusted selection only; trusted teaching context is resolved by the server."""

    course_id: ID
    content_version: ID
    unit_id: ID
    lesson_id: ID
    protocol: Literal["rahbar.v1"] = "rahbar.v1"
    visuals: bool = True


class SessionState(Model):
    session_id: ID
    connection_epoch: ID
    generation: Count
    context: TeachingContext
    cursor: LessonCursor
    active_attempt_id: ID | None = None
    capabilities: Capabilities
    paused: bool


class BakedAudio(Model):
    path: Text
    bake_hash: ID
    format: AudioFormat


class LessonSentence(ApprovedSentence):
    audio: BakedAudio | None = None


class PreparedLesson(Model):
    lesson_id: ID
    lesson_version: ID
    unit_id: ID
    content_version: ID
    sentences: list[LessonSentence] = Field(min_length=1)


class QuizItem(Model):
    item_id: ID
    question: Text
    options: dict[str, Text] = Field(min_length=2)


class PrivateQuizItem(QuizItem):
    expected_choice: ID

    @model_validator(mode="after")
    def expected_exists(self) -> PrivateQuizItem:
        if self.expected_choice not in self.options:
            raise ValueError("expected choice must identify an option")
        return self

    def public(self) -> QuizItem:
        return QuizItem.model_validate(self.model_dump(exclude={"expected_choice"}))


class VivaPrompt(Model):
    prompt_id: ID
    text: Text
    clarification: Text | None = None
    required_key_points: dict[str, Text] = Field(min_length=1)
    evidence_ids: list[ID] = Field(min_length=1)


class VivaDefinition(Model):
    unit_id: ID
    content_version: ID
    assessment_version: ID
    prompts: list[VivaPrompt] = Field(min_length=1)


class VivaCreate(Model):
    session_id: ID
    unit_id: ID
    content_version: ID


class VivaResult(Model):
    attempt_id: ID
    unit_id: ID
    content_version: ID
    assessment_version: ID
    outcome: Outcome
    feedback: Text


class PrivateVivaResult(VivaResult):
    item_status: dict[str, Outcome]
    transcript_references: list[ID]
    matched_key_points: list[ID]
    missing_key_points: list[ID]
    evaluator_version: ID

    def public(self) -> VivaResult:
        return VivaResult.model_validate({k: getattr(self, k) for k in VivaResult.model_fields})


class ProviderFailure(Model):
    code: Literal["timeout", "throttled", "quota_exhausted", "authentication", "unavailable"]
    recoverable: bool
    fallback_ready: bool
    provider: ID


class ChannelJob(Model):
    business_id: ID
    sender_id: ID
    message_id: ID
    state: Literal["pending", "processing", "completed", "failed"]


class QuizDefinition(Model):
    unit_id: ID
    content_version: ID
    assessment_version: ID
    items: list[PrivateQuizItem] = Field(min_length=1)


class PublicQuiz(Model):
    unit_id: ID
    content_version: ID
    assessment_version: ID
    items: list[QuizItem] = Field(min_length=1)


class Health(Model):
    status: Literal["ok", "unavailable"]
    profile: Literal["mock", "real", "evaluation"]
    capabilities: dict[str, bool]
