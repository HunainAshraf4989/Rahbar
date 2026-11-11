"""Single-worker local lecture sessions using the existing rahbar.v1 contracts."""

import asyncio
import base64
import io
import json
import sqlite3
import wave
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from rahbar.contracts.events import parse_event
from rahbar.contracts.models import (
    ApprovedSentence,
    AudioFormat,
    Capabilities,
    LessonCursor,
    PreparedLesson,
    SessionState,
    TeachingContext,
)
from rahbar.contracts.ports import TurnContext
from rahbar.engine.audio import InputError, pcm_to_wav
from rahbar.engine.bake import cached_pcm
from rahbar.engine.pipeline import Pipeline
from rahbar.engine.runtime import ProviderError


class SessionStore:
    """Persist only cursor/context metadata, never learner audio or transcripts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT)")

    def save(self, state: SessionState) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)",
                       (state.session_id, state.model_dump_json()))

    def get(self, session_id: str) -> SessionState:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return SessionState.model_validate_json(row[0])

    def create(self, lesson: PreparedLesson, visuals: bool) -> SessionState:
        state = SessionState(
            session_id=uuid4().hex, connection_epoch=uuid4().hex, generation=0, paused=True,
            context=TeachingContext(course_id="relay-demo", content_version=lesson.content_version,
                                    unit_id=lesson.unit_id, lesson_id=lesson.lesson_id, mode="tutor"),
            cursor=LessonCursor(cursor_id=uuid4().hex, revision=0, unit_id=lesson.unit_id,
                                content_version=lesson.content_version, lesson_id=lesson.lesson_id,
                                lesson_version=lesson.lesson_version,
                                restart_sentence_id=lesson.sentences[0].sentence_id),
            capabilities=Capabilities(input_audio=AudioFormat(sample_rate=16000),
                                      max_utterance_ms=30000, max_queue_chunks=300,
                                      stt_streaming=False, visuals=visuals, profile="real"))
        self.save(state)
        return state


@dataclass
class PlaybackTicket:
    sentence_id: str
    stream_id: str
    kind: str
    started: bool = False
    sent: bool = False


class LectureConnection:
    def __init__(self, socket: WebSocket, state: SessionState, store: SessionStore,
                 lesson: PreparedLesson, directory: Path, pipeline: Pipeline) -> None:
        self.socket, self.state, self.store = socket, state, store
        self.lesson, self.directory, self.pipeline = lesson, directory, pipeline
        self.context: TurnContext | None = None
        self.task: asyncio.Task[None] | None = None
        self.ticket: PlaybackTicket | None = None
        self.input = bytearray()
        self.input_stream: str | None = None
        self.input_chunks = 0
        self.listening = False
        self.sequence = 0
        self.client_sequence = -1
        self.event_ids: set[str] = set()
        self.turn_ids: set[str] = set()
        self.send_lock = asyncio.Lock()
        self.closed = False

    def save(self, **changes: Any) -> None:
        self.state = self.state.model_copy(update=changes)
        self.store.save(self.state)

    def cancel(self) -> None:
        if self.context is not None:
            self.context.cancelled.set()
        if self.task is not None:
            self.task.cancel()
        self.ticket = None
        self.input.clear()
        self.input_chunks = 0
        self.input_stream = None
        self.listening = False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cancel()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
        self.save(paused=True)

    async def emit(self, kind: str, payload: dict[str, Any],
                   context: TurnContext | None = None) -> None:
        async with self.send_lock:
            if context is not None and (context is not self.context or context.cancelled.is_set()):
                return
            event = dict(protocol="rahbar.v1", type=kind, event_id=uuid4().hex,
                         session_id=self.state.session_id,
                         connection_epoch=self.state.connection_epoch,
                         generation=self.state.generation,
                         turn_id=context.turn_id if context else None,
                         sequence=self.sequence, payload=payload)
            parse_event(event, "server")
            self.sequence += 1
            # A disconnected/slow consumer must not hold provider work indefinitely.
            try:
                async with asyncio.timeout(5):
                    await self.socket.send_json(event)
            except (TimeoutError, WebSocketDisconnect, RuntimeError):
                if self.context is not None:
                    self.context.cancelled.set()
                self.ticket = None
                self.save(paused=True)
                raise asyncio.CancelledError from None

    async def snapshot(self) -> None:
        await self.emit("session.state", self.state.model_dump())

    async def error(self, code: str) -> None:
        await self.emit("error", dict(code=code, recoverable=True,
                                     message="The operation could not be completed. Retry or resume.",
                                     correlation_id=uuid4().hex))

    async def start(self, turn_id: str, *, listening: bool) -> TurnContext:
        self.cancel()
        self.save(generation=self.state.generation + 1, paused=listening)
        self.context = TurnContext(self.state.session_id, turn_id, self.state.generation,
                                   self.state.connection_epoch, asyncio.Event())
        self.listening = listening
        await self.emit("turn.started", {}, self.context)
        return self.context

    async def play(self) -> None:
        sentence_id = self.state.cursor.restart_sentence_id
        if sentence_id is None:
            self.save(paused=True)
            await self.snapshot()
            return
        sentence = next(s for s in self.lesson.sentences if s.sentence_id == sentence_id)
        context = await self.start(uuid4().hex, listening=False)
        self.task = asyncio.create_task(self.play_saved(sentence, context))

    async def play_saved(self, sentence: ApprovedSentence, context: TurnContext) -> None:
        try:
            baked = next(s for s in self.lesson.sentences if s.sentence_id == sentence.sentence_id)
            if baked.audio is None:
                raise InputError("Missing prepared audio")
            await self.audio(sentence, cached_pcm(self.directory / Path(baked.audio.path).name),
                             "lesson", context)
        except (OSError, ValueError, wave.Error, EOFError):
            if context is self.context and not context.cancelled.is_set():
                self.cancel()
                self.save(paused=True)
                await self.error("lecture_audio_unavailable")

    async def audio(self, sentence: ApprovedSentence, pcm: bytes,
                    kind: str, context: TurnContext) -> None:
        if context is not self.context or context.cancelled.is_set():
            return
        ticket = PlaybackTicket(sentence.sentence_id, uuid4().hex, kind)
        self.ticket = ticket
        public = ApprovedSentence.model_validate(sentence.model_dump(exclude={"audio"}))
        if not self.state.capabilities.visuals:
            public = public.model_copy(update={"visual_target": None})
        await self.emit("sentence.approved", public.model_dump(), context)
        await self.emit("audio.start", dict(sentence_id=ticket.sentence_id,
                                            stream_id=ticket.stream_id, encoding="pcm_s16le",
                                            sample_rate=24000, channels=1), context)
        chunks = [pcm[i:i + 4800] for i in range(0, len(pcm), 4800)]
        for n, chunk in enumerate(chunks):
            await self.emit("audio.chunk", dict(sentence_id=ticket.sentence_id,
                                                stream_id=ticket.stream_id, chunk_sequence=n,
                                                data=base64.b64encode(chunk).decode()), context)
        ticket.sent = True
        await self.emit("audio.end", dict(sentence_id=ticket.sentence_id,
                                          stream_id=ticket.stream_id,
                                          last_chunk_sequence=len(chunks) - 1), context)

    async def answer(self, audio: bytes, context: TurnContext) -> None:
        try:
            async with asyncio.timeout(100):
                result = await self.pipeline.run(audio, self.state.context, context)
            if context is not self.context or context.cancelled.is_set():
                return
            if len(result.sentences) != 1:
                raise InputError("Demo expects one checked answer segment")
            await self.emit("transcript.final", dict(canonical_text=result.transcript,
                                                      hypothesis_revision=0, final=True), context)
            with wave.open(io.BytesIO(result.wav), "rb") as wav:
                pcm = wav.readframes(wav.getnframes())
            await self.audio(result.sentences[0], pcm, "answer", context)
            await self.emit("turn.completed", dict(outcome=result.outcome,
                restart_sentence_id=self.state.cursor.restart_sentence_id), context)
        except (InputError, ProviderError, TimeoutError) as exc:
            if context is self.context and not context.cancelled.is_set():
                code = exc.failure.code if isinstance(exc, ProviderError) else "turn_failed"
                self.ticket = None
                self.save(paused=True)
                await self.error(code)
                await self.emit("turn.completed", dict(outcome="failed",
                    restart_sentence_id=self.state.cursor.restart_sentence_id), context)

    async def receive(self, raw: dict[str, Any]) -> None:
        if self.closed:
            return
        event = parse_event(raw, "client").model_dump()
        if (event["session_id"] != self.state.session_id
                or event["connection_epoch"] != self.state.connection_epoch):
            await self.error("stale_connection")
            return
        if event["event_id"] in self.event_ids:
            return
        if event["sequence"] <= self.client_sequence:
            await self.error("stale_sequence")
            return
        self.client_sequence = event["sequence"]
        self.event_ids.add(event["event_id"])
        # The sequence fence still rejects older events after bounded dedup eviction.
        if len(self.event_ids) > 4096:
            self.event_ids = {event["event_id"]}
        kind, payload = event["type"], event["payload"]
        if kind == "session.resume":
            await self.snapshot()
            return
        if kind == "speech.start":
            if event["turn_id"] in self.turn_ids:
                await self.error("reused_turn")
                return
            self.turn_ids.add(event["turn_id"])
            if len(self.turn_ids) > 4096:
                await self.error("session_limit")
                await self.socket.close(code=1008)
                return
            await self.start(event["turn_id"], listening=True)
            return
        pending_audio = (kind in {"audio.input", "speech.end"} and self.listening
                         and self.context is not None and event["turn_id"] == self.context.turn_id)
        if event["generation"] != self.state.generation and not pending_audio:
            await self.error("stale_generation")
            return
        if kind == "lesson.control":
            action = payload["action"]
            if action == "pause":
                self.cancel()
                self.save(paused=True)
                await self.snapshot()
                return
            requested = payload["sentence_id"]
            current = self.state.cursor.restart_sentence_id
            if action == "repeat" and current is None:
                current = self.lesson.sentences[-1].sentence_id
            if requested is not None and requested != current:
                await self.error("invalid_lesson_position")
                return
            if action == "repeat" and self.state.cursor.restart_sentence_id is None:
                self.save(cursor=self.state.cursor.model_copy(update={
                    "restart_sentence_id": current, "revision": self.state.cursor.revision + 1}))
            await self.play()
            return
        context = self.context
        if context is None or event["turn_id"] != context.turn_id or context.cancelled.is_set():
            await self.error("stale_turn")
            return
        if kind == "turn.cancel":
            self.cancel()
            self.save(paused=True)
            await self.snapshot()
        elif kind == "audio.input":
            if not self.listening or payload["chunk_sequence"] != self.input_chunks:
                raise InputError("Invalid input sequence")
            if self.input_stream is None:
                self.input_stream = payload["stream_id"]
            if payload["stream_id"] != self.input_stream:
                raise InputError("Mismatched input stream")
            self.input.extend(base64.b64decode(payload["data"], validate=True))
            self.input_chunks += 1
            if len(self.input) > 960000 or self.input_chunks > 300:
                raise InputError("Utterance exceeds limit")
        elif kind == "speech.end":
            if (not self.listening or not self.input_chunks
                    or payload["last_chunk_sequence"] != self.input_chunks - 1):
                raise InputError("Invalid utterance end")
            self.listening = False
            audio = pcm_to_wav(bytes(self.input), 16000)
            self.input.clear()
            self.task = asyncio.create_task(self.answer(audio, context))
        elif kind.startswith("playback."):
            ticket = self.ticket
            if (ticket is None or payload["sentence_id"] != ticket.sentence_id
                    or payload["stream_id"] != ticket.stream_id):
                await self.error("stale_playback")
                return
            if kind == "playback.interrupted":
                self.cancel()
                self.save(paused=True)
                await self.snapshot()
            elif kind == "playback.started":
                ticket.started = True
                if ticket.kind == "lesson":
                    sentence = next(s for s in self.lesson.sentences
                                    if s.sentence_id == ticket.sentence_id)
                    self.save(context=self.state.context.model_copy(update={
                        "confirmed_sentence_id": sentence.sentence_id,
                        "visual_context": (sentence.visual_target
                                           if self.state.capabilities.visuals else None)}))
            elif ticket.started and ticket.sent:
                self.ticket = None
                if ticket.kind == "lesson":
                    index = next(i for i, s in enumerate(self.lesson.sentences)
                                 if s.sentence_id == ticket.sentence_id)
                    following = (self.lesson.sentences[index + 1].sentence_id
                                 if index + 1 < len(self.lesson.sentences) else None)
                    self.save(cursor=self.state.cursor.model_copy(update={
                        "last_completed_sentence_id": ticket.sentence_id,
                        "restart_sentence_id": following,
                        "revision": self.state.cursor.revision + 1}))
                await self.play()
            else:
                await self.error("playback_not_started")
        else:
            await self.error("unsupported_event")

    async def run(self) -> None:
        self.save(connection_epoch=uuid4().hex, generation=self.state.generation + 1, paused=True)
        await self.snapshot()
        try:
            while True:
                try:
                    text = await self.socket.receive_text()
                    if len(text) > 100000:
                        raise InputError("Oversized event")
                    await self.receive(json.loads(text))
                except (ValueError, TypeError, KeyError):
                    self.cancel()
                    self.save(paused=True)
                    await self.error("invalid_event")
        except WebSocketDisconnect:
            pass
        finally:
            await self.close()
