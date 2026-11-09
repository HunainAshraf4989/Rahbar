"""The event catalog is shared by validation, schema export, and both consumers."""

import base64
import binascii
from typing import Literal

from pydantic import BaseModel, create_model, field_validator

from .models import (
    ID,
    ApprovedSentence,
    AudioFormat,
    Count,
    Model,
    SessionState,
    Text,
    VivaResult,
)


class Resume(Model):
    cursor_revision: Count


class LessonControl(Model):
    action: Literal["play", "pause", "repeat"]
    sentence_id: ID | None = None


class SpeechStart(Model):
    playback_sentence_id: ID | None = None


class Empty(Model):
    pass


class AudioInput(Model):
    stream_id: ID
    chunk_sequence: Count
    data: Text

    @field_validator("data")
    @classmethod
    def pcm_base64(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid base64 PCM") from exc
        if not decoded or len(decoded) % 2:
            raise ValueError("PCM16 must contain whole samples")
        return value


class AudioChunk(AudioInput):
    sentence_id: ID


class AudioStart(AudioFormat):
    sentence_id: ID
    stream_id: ID


class SpeechEnd(Model):
    last_chunk_sequence: Count


class AudioEnd(SpeechEnd):
    sentence_id: ID
    stream_id: ID


class Cancel(Model):
    reason: Text


class TextQuestion(Model):
    text: Text


class VivaControl(Model):
    attempt_id: ID
    action: Literal["repeat", "clarify", "exit", "resume"]


class Transcript(Model):
    canonical_text: str
    hypothesis_revision: Count


class PartialTranscript(Transcript):
    final: Literal[False] = False


class FinalTranscript(Transcript):
    final: Literal[True] = True


class Playback(Model):
    sentence_id: ID
    stream_id: ID
    client_monotonic_ms: float
    interrupted_sample: Count | None = None


class TurnCompleted(Model):
    outcome: Literal["answered", "refused", "cancelled", "failed"]
    restart_sentence_id: ID | None = None


class VivaState(Model):
    attempt_id: ID
    prompt_id: ID | None = None
    state: Literal["prompt", "listening", "incomplete", "completed"]


class Error(Model):
    code: ID
    recoverable: bool
    message: Text
    correlation_id: ID


class Envelope(Model):
    protocol: Literal["rahbar.v1"] = "rahbar.v1"
    event_id: ID
    session_id: ID
    connection_epoch: ID
    generation: Count
    turn_id: ID | None = None
    sequence: Count


CLIENT_PAYLOADS: dict[str, type[BaseModel]] = {
    "session.resume": Resume,
    "lesson.control": LessonControl,
    "speech.start": SpeechStart,
    "audio.input": AudioInput,
    "speech.end": SpeechEnd,
    "turn.cancel": Cancel,
    "question.text": TextQuestion,
    "viva.control": VivaControl,
    "playback.started": Playback,
    "playback.ended": Playback,
    "playback.interrupted": Playback,
}
SERVER_PAYLOADS: dict[str, type[BaseModel]] = {
    "session.state": SessionState,
    "turn.started": Empty,
    "transcript.partial": PartialTranscript,
    "transcript.final": FinalTranscript,
    "sentence.approved": ApprovedSentence,
    "audio.start": AudioStart,
    "audio.chunk": AudioChunk,
    "audio.end": AudioEnd,
    "turn.completed": TurnCompleted,
    "viva.state": VivaState,
    "viva.result": VivaResult,
    "error": Error,
}
SESSION_EVENTS = {
    "session.resume",
    "session.state",
    "lesson.control",
    "error",
    "viva.control",
    "viva.state",
    "viva.result",
}
EVENT_MODELS: dict[str, type[BaseModel]] = {}
for kind, payload in (CLIENT_PAYLOADS | SERVER_PAYLOADS).items():
    name = "".join(word.title() for word in kind.split(".")) + "Event"
    fields = {"type": (Literal[kind], ...), "payload": (payload, ...)}
    if kind not in SESSION_EVENTS:
        fields["turn_id"] = (ID, ...)
    EVENT_MODELS[kind] = create_model(name, __base__=Envelope, **fields)  # type: ignore[call-overload]


def parse_event(data: dict[str, object], direction: Literal["client", "server"]) -> BaseModel:
    kind = data.get("type")
    allowed = CLIENT_PAYLOADS if direction == "client" else SERVER_PAYLOADS
    if not isinstance(kind, str) or kind not in allowed:
        raise ValueError("unknown event or wrong direction")
    return EVENT_MODELS[kind].model_validate(data)
