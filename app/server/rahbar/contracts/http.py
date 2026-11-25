"""HTTP wire types and operation descriptions; no runtime handlers are defined here."""

import builtins
from typing import Literal

from pydantic import Field, SecretStr

from .models import (
    ID,
    AssetManifest,
    CurriculumUnit,
    Health,
    LessonCursor,
    Model,
    Outcome,
    PreparedLesson,
    PublicQuiz,
    SessionCreate,
    SessionState,
    Text,
    VivaCreate,
    VivaResult,
)


class Credentials(Model):
    email: Text
    password: SecretStr


class Registration(Credentials):
    full_name: str = Field(min_length=1, max_length=120)
    phone_number: str = Field(min_length=7, max_length=40)


class Account(Model):
    account_id: ID
    email: Text
    full_name: Text | None = None
    phone_number: Text | None = None


class Curriculum(Model):
    course_id: ID
    content_version: ID
    units: list[CurriculumUnit]


class QuizSubmit(Model):
    unit_id: ID
    content_version: ID
    assessment_version: ID
    choices: dict[str, str]


class QuizResult(Model):
    attempt_id: ID
    unit_id: ID
    content_version: ID
    assessment_version: ID
    outcome: Outcome
    feedback: Text


class UnitProgress(Model):
    unit_id: ID
    content_version: ID
    cursor: LessonCursor | None = None
    quiz_attempt_id: ID | None = None
    viva_attempt_id: ID | None = None
    finished: bool


class Progress(Model):
    units: list[UnitProgress]


class HttpError(Model):
    code: ID
    message: Text
    correlation_id: ID


class WebhookAccepted(Model):
    status: Literal["accepted"]


class WhatsAppWebhook(Model):
    """Provider payload remains untrusted until raw-body signature verification."""

    object: Text
    entry: list[dict[str, builtins.object]] = Field(default_factory=list)


# method, path, request model, response model, success status, requires idempotency
OPERATIONS = [
    ("get", "/health/live", None, Health, 200, False),
    ("get", "/health/ready", None, Health, 200, False),
    ("post", "/api/v1/auth/register", Registration, Account, 201, False),
    ("post", "/api/v1/auth/login", Credentials, Account, 200, False),
    ("post", "/api/v1/auth/logout", None, None, 204, False),
    ("get", "/api/v1/me", None, Account, 200, False),
    ("get", "/api/v1/curriculum", None, Curriculum, 200, False),
    ("get", "/api/v1/units/{unit_id}/lesson", None, PreparedLesson, 200, False),
    ("get", "/api/v1/units/{unit_id}/quiz", None, PublicQuiz, 200, False),
    ("post", "/api/v1/quiz-attempts", QuizSubmit, QuizResult, 201, True),
    ("get", "/api/v1/progress", None, Progress, 200, False),
    ("post", "/api/v1/engine/sessions", SessionCreate, SessionState, 201, True),
    ("get", "/api/v1/engine/sessions/{session_id}", None, SessionState, 200, False),
    ("post", "/api/v1/viva-attempts", VivaCreate, VivaResult, 201, True),
    ("get", "/api/v1/viva-attempts/{attempt_id}", None, VivaResult, 200, False),
    ("get", "/api/v1/assets/{asset_id}/manifest", None, AssetManifest, 200, False),
    ("post", "/webhooks/whatsapp", WhatsAppWebhook, WebhookAccepted, 200, False),
]
