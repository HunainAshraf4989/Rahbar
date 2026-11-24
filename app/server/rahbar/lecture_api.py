"""Local-only lecture endpoints; accounts remain the platform owner's boundary."""

import asyncio
import hashlib
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse

from rahbar.contracts.models import AssetManifest, PreparedLesson, SessionCreate, SessionState
from rahbar.engine.audio import InputError
from rahbar.engine.bake import cached_pcm
from rahbar.engine.conversation import LessonConversations
from rahbar.engine.lesson import relay_asset, relay_lesson
from rahbar.engine.pipeline import Pipeline
from rahbar.engine.sessions import LectureConnection, SessionStore


class LectureGateway:
    def __init__(self, root: Path, directory: Path, pipeline: Pipeline,
                 store: SessionStore | None = None) -> None:
        self.directory, self.pipeline = directory, pipeline
        self.asset = relay_asset(root)
        expected = relay_lesson(self.asset, root)
        self.lesson = PreparedLesson.model_validate_json((directory / "lesson.json").read_text())
        stripped = self.lesson.model_copy(update={"sentences": [
            s.model_copy(update={"audio": None}) for s in self.lesson.sentences]})
        if stripped != expected:
            raise InputError("Prepared lesson is stale; run prepare_lecture.py")
        self.audio_files = {}
        for sentence in self.lesson.sentences:
            if sentence.audio is None:
                raise InputError("Missing lesson audio")
            name = Path(sentence.audio.path).name
            if name != sentence.audio.bake_hash + ".wav":
                raise InputError("Invalid baked audio path")
            path = directory / name
            cached_pcm(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != path.with_suffix(".sha256").read_text():
                raise InputError("Corrupt baked audio")
            self.audio_files[name] = path
        self.asset_files = {Path(url).name: root / "asset_gen/out" / Path(url).name
                            for view in self.asset.views for url in (view.image, view.overlay)}
        self.store = store if store is not None else SessionStore(directory / "sessions.sqlite3")
        self.connections: dict[str, LectureConnection] = {}
        self.conversations = LessonConversations()

    async def state(self, session_id: str) -> SessionState:
        try:
            state = await asyncio.to_thread(self.store.get, session_id)
        except KeyError:
            raise HTTPException(404, "session_not_found") from None
        if state.cursor.lesson_version != self.lesson.lesson_version:
            raise HTTPException(409, "lesson_version_changed")
        return state


def register_lecture_routes(app: FastAPI) -> None:
    router = APIRouter()

    def gateway(request: Request | WebSocket) -> LectureGateway:
        value: LectureGateway | None = getattr(request.app.state, "lecture", None)
        if value is None:
            raise HTTPException(503, "lecture_not_prepared")
        return value

    @router.get("/dev/lecture", response_model=PreparedLesson)
    async def lesson(request: Request) -> PreparedLesson:
        return gateway(request).lesson

    @router.get("/dev/lecture/asset", response_model=AssetManifest)
    async def asset(request: Request) -> AssetManifest:
        return gateway(request).asset

    @router.get("/dev/lesson-audio/{filename}")
    async def audio(filename: str, request: Request) -> FileResponse:
        path = gateway(request).audio_files.get(filename)
        if path is None:
            raise HTTPException(404, "audio_not_found")
        return FileResponse(path, media_type="audio/wav")

    @router.get("/dev/assets/{filename}")
    async def asset_file(filename: str, request: Request) -> FileResponse:
        path = gateway(request).asset_files.get(filename)
        if path is None:
            raise HTTPException(404, "asset_not_found")
        return FileResponse(path)

    @router.post("/dev/lecture/sessions", response_model=SessionState, status_code=201)
    async def create(selection: SessionCreate, request: Request) -> SessionState:
        service = gateway(request)
        expected = ("relay-demo", service.lesson.content_version,
                    service.lesson.unit_id, service.lesson.lesson_id)
        if (selection.course_id, selection.content_version,
                selection.unit_id, selection.lesson_id) != expected:
            raise HTTPException(409, "lesson_selection_mismatch")
        return await asyncio.to_thread(service.store.create, service.lesson, selection.visuals)

    @router.get("/dev/lecture/sessions/{session_id}", response_model=SessionState)
    async def session(session_id: str, request: Request) -> SessionState:
        return await gateway(request).state(session_id)

    @router.delete("/dev/lecture/sessions/{session_id}/conversation", status_code=204)
    async def end_conversation(session_id: str, request: Request) -> None:
        service = gateway(request)
        await service.state(session_id)
        connection = service.connections.get(session_id)
        if connection is not None:
            await connection.close()
            await connection.socket.close(code=1000)
        service.conversations.end(session_id)

    @router.websocket("/dev/lecture/sessions/{session_id}/events")
    async def events(socket: WebSocket, session_id: str) -> None:
        try:
            service = gateway(socket)
            await service.state(session_id)
        except HTTPException:
            await socket.close(code=1008)
            return
        await socket.accept()
        previous = service.connections.get(session_id)
        if previous is not None:
            await previous.close()
            await previous.socket.close(code=1000)
        state = await service.state(session_id)
        conversation = service.conversations.get(session_id, state.context.content_version,
                                                 state.context.lesson_id)
        connection = LectureConnection(socket, state, service.store,
                                       service.lesson, service.directory, service.pipeline, conversation)
        service.connections[session_id] = connection
        try:
            await connection.run()
        finally:
            if service.connections.get(session_id) is connection:
                del service.connections[session_id]

    app.include_router(router)
