"""Opt-in local text RAG app; no unauthenticated production deployment."""
import asyncio

from fastapi import FastAPI, HTTPException
from pydantic import Field

from rahbar.retrieval.answer import AnswerService, StrictModel, TextAnswer
from rahbar.retrieval.gemini import QuotaExhausted
from rahbar.retrieval.postgres import PostgresTextSessions
from rahbar.retrieval.sessions import SessionConflict, TextSessions


class TextQuestion(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    revision: int = Field(ge=0)


class TurnAnswer(TextAnswer):
    revision: int = Field(ge=1)


def create_text_app(service: AnswerService, sessions: TextSessions | PostgresTextSessions, version: str) -> FastAPI:
    app = FastAPI(title='Rahbar experimental text RAG')

    @app.post('/dev/rag/sessions', status_code=201)
    async def create() -> dict[str, str | int]:
        identifier = await asyncio.to_thread(sessions.create, version)
        return {'session_id': identifier, 'revision': 0, 'release_version': version}

    @app.post('/dev/rag/sessions/{session_id}/turns', response_model=TurnAnswer)
    async def turn(session_id: str, request: TextQuestion) -> TurnAnswer:
        try:
            revision, history = await asyncio.to_thread(sessions.load, session_id, version)
            if revision != request.revision:
                raise SessionConflict('Stale session revision')
            result = await service.answer(request.question, history)
            await asyncio.to_thread(sessions.append, session_id, version, revision, history,
                                    request.question, result.text)
            return TurnAnswer(**result.model_dump(), revision=revision + 1)
        except SessionConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except QuotaExhausted:
            raise HTTPException(503, 'Free model quota unavailable; retry later') from None

    return app
