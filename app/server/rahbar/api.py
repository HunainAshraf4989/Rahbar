"""Local integration endpoint. Not the authenticated production session gateway."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from rahbar.bootstrap import build_pipeline
from rahbar.config import load_settings
from rahbar.contracts.models import TeachingContext
from rahbar.contracts.ports import TurnContext
from rahbar.engine.audio import InputError
from rahbar.engine.lesson import relay_passages
from rahbar.engine.pipeline import Pipeline
from rahbar.engine.runtime import ProviderError
from rahbar.lecture_api import LectureGateway, register_lecture_routes

logger = logging.getLogger("rahbar.turns")


def create_app(pipeline: Pipeline | None = None, *, project_root: Path | None = None,
               lecture_directory: Path | None = None) -> FastAPI:
    root = project_root or Path.cwd()
    directory = lecture_directory or root / ".cache/relay-lecture"

    def prepare_gateway(app: FastAPI) -> None:
        app.state.lecture = None
        if (directory / "lesson.json").exists():
            app.state.lecture = LectureGateway(root, directory, app.state.pipeline)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if pipeline is not None:
            app.state.pipeline = pipeline
            app.state.answer_mode = "dummy"
            app.state.teaching = TeachingContext(course_id="dummy", content_version="dummy-v1",
                                                unit_id="dummy", lesson_id="dummy", mode="tutor")
            prepare_gateway(app)
            yield
            return
        settings = load_settings()
        async with httpx.AsyncClient(follow_redirects=False) as client:
            app.state.pipeline, app.state.teaching = build_pipeline(settings, client,
                                                                    relay_passages(root))
            app.state.answer_mode = settings.answer_mode
            prepare_gateway(app)
            yield

    app = FastAPI(title="Rahbar local voice integration", lifespan=lifespan)
    register_lecture_routes(app)

    @app.middleware("http")
    async def lecture_headers(request: Request,
                              call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/dev/lecture"):
            response.headers["X-Content-Review"] = "draft"
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "answer_mode": app.state.answer_mode, "stt_streaming": "false"}

    @app.post("/dev/voice-turn", response_class=Response)
    async def voice_turn(request: Request) -> Response:
        turn_id = uuid4().hex
        headers = {"Cache-Control": "no-store", "X-Turn-ID": turn_id}
        if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
            return JSONResponse({"code": "expected_audio_wav"}, 415, headers=headers)
        audio = bytearray()
        async for chunk in request.stream():
            audio.extend(chunk)
            if len(audio) > 960_044:
                return JSONResponse({"code": "audio_too_large"}, 413, headers=headers)
        context = TurnContext(turn_id, turn_id, 0, turn_id, asyncio.Event())
        teaching = app.state.teaching
        async def disconnect() -> None:
            while not await request.is_disconnected():
                await asyncio.sleep(0.1)
            context.cancelled.set()
        monitor = asyncio.create_task(disconnect())
        try:
            async with asyncio.timeout(100):
                result = await app.state.pipeline.run(bytes(audio), teaching, context)
            logger.info("turn=%s outcome=%s timings_ms=%s providers=%s", turn_id, result.outcome,
                        result.timings_ms, [(p.provider, p.model, p.mock) for p in result.providers])
            return Response(result.wav, media_type="audio/wav", headers={
                **headers, "X-Outcome": result.outcome, "X-Answer-Mode": app.state.answer_mode,
            })
        except InputError as exc:
            return JSONResponse({"code": "invalid_turn", "message": str(exc)}, 422, headers=headers)
        except ProviderError as exc:
            return JSONResponse(exc.failure.model_dump(), 502, headers=headers)
        except TimeoutError:
            return JSONResponse({"code": "turn_timeout"}, 504, headers=headers)
        except asyncio.CancelledError:
            context.cancelled.set()
            raise
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    return app
