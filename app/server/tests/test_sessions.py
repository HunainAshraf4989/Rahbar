import asyncio
import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from rahbar.api import create_app
from rahbar.bootstrap import build_pipeline
from rahbar.config import Settings
from rahbar.contracts.events import parse_event
from rahbar.engine.bake import bake_lesson
from rahbar.engine.lesson import relay_asset, relay_lesson, relay_passages
from rahbar.engine.sessions import SessionStore
from rahbar.providers.elevenlabs import ElevenLabs
from test_pipeline import context

ROOT = Path(__file__).resolve().parents[3]
SELECTION = dict(course_id="relay-demo", content_version="relay-demo-v1", unit_id="relay",
                 lesson_id="relay-demo")


@pytest.fixture
def prepared(tmp_path):
    calls = []
    control = {"selection": "relay-coil", "fail": False}
    def handler(request):
        calls.append(request)
        if control["fail"]:
            return httpx.Response(503, json={"private": "never expose this"})
        if request.url.path.endswith("speech-to-text"):
            return httpx.Response(200, json={"text": "coil کیا کرتی ہے؟"})
        if request.url.path.endswith(":generateContent"):
            body = json.loads(request.content)
            prompt = json.loads(body["contents"][0]["parts"][0]["text"])
            assert len(prompt["passages"]) == 7
            assert prompt["teaching_context"]["unit_id"] == "relay"
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps({"passage_id": control["selection"]})}]}}]})
        return httpx.Response(200, content=b"\x01\x00" * 2400,
                              headers={"content-type": "audio/pcm"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings("test", "stock", gemini_api_key="test", answer_mode="gemini")
    pipeline, _ = build_pipeline(settings, client, relay_passages(ROOT))
    lesson = asyncio.run(bake_lesson(relay_lesson(relay_asset(ROOT), ROOT),
                                     ElevenLabs(settings, client), settings, tmp_path, context()))
    calls.clear()
    yield pipeline, lesson, tmp_path, calls, control
    asyncio.run(client.aclose())


class Player:
    def __init__(self, socket):
        self.socket = socket
        self.sequence = 0
        first = self.read()
        assert first["type"] == "session.state"
        self.state = first["payload"]

    def read(self):
        event = self.socket.receive_json()
        parse_event(event, "server")
        self.session_id = event["session_id"]
        self.epoch = event["connection_epoch"]
        self.generation = event["generation"]
        if event["turn_id"] is not None:
            self.turn = event["turn_id"]
        return event

    def send(self, kind, payload, **overrides):
        self.sequence += 1
        event = dict(protocol="rahbar.v1", type=kind, event_id=uuid4().hex,
                     session_id=self.session_id, connection_epoch=self.epoch,
                     generation=self.generation, turn_id=getattr(self, "turn", None),
                     sequence=self.sequence, payload=payload)
        event.update(overrides)
        parse_event(event, "client")
        self.socket.send_json(event)
        return event

    def audio(self):
        events = []
        while True:
            event = self.read()
            events.append(event)
            assert event["type"] != "error", event
            if event["type"] == "audio.end":
                return events

    def ack(self, end, kind="playback.ended"):
        payload = {k: end["payload"][k] for k in ("sentence_id", "stream_id")}
        payload.update(client_monotonic_ms=1.0, interrupted_sample=None)
        return self.send(kind, payload, turn_id=end["turn_id"], generation=end["generation"])

    def question(self, turn="question"):
        self.send("speech.start", {"playback_sentence_id": None}, turn_id=turn)
        # Deliberately send PCM before receiving turn.started (generation is a stale hint).
        self.send("audio.input", dict(stream_id=turn, chunk_sequence=0,
                  data=base64.b64encode(b"\x01\x00" * 1600).decode()), turn_id=turn)
        self.send("speech.end", {"last_chunk_sequence": 0}, turn_id=turn)


def session(client):
    response = client.post("/dev/lecture/sessions", json=SELECTION)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def test_lecture_http_and_complete_playback(prepared):
    pipeline, lesson, directory, calls, _ = prepared
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        response = client.get("/dev/lecture")
        assert response.headers["x-content-review"] == "draft"
        assert response.json() == lesson.model_dump()
        assert client.get(lesson.sentences[0].audio.path).content[:4] == b"RIFF"
        asset = client.get("/dev/lecture/asset").json()
        assert client.get(asset["views"][0]["overlay"]).status_code == 200
        assert client.get("/dev/assets/relay_5pin.png").status_code == 404
        assert client.get("/dev/lesson-audio/missing.wav").status_code == 404
        assert client.post("/dev/lecture/sessions", json={**SELECTION, "unit_id": "other"}).status_code == 409
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            player.send("lesson.control", dict(action="play", sentence_id=None))
            for sentence in lesson.sentences:
                end = player.audio()[-1]
                assert end["payload"]["sentence_id"] == sentence.sentence_id
                player.ack(end, "playback.started")
                player.ack(end)
            state = player.read()["payload"]
            assert state["cursor"]["restart_sentence_id"] is None
            assert state["cursor"]["revision"] == 7
            assert state["paused"]
        assert client.get(f"/dev/lecture/sessions/{sid}").json()["cursor"] == state["cursor"]
    assert not calls  # every lecture playback was cached, no paid requests
    assert SessionStore(directory / "sessions.sqlite3").get(sid).cursor.revision == 7


@pytest.mark.parametrize("selected,outcome", [("relay-coil", "answered"), (None, "refused")])
def test_interrupt_answer_resume_and_stale_ack(prepared, selected, outcome):
    pipeline, lesson, directory, calls, control = prepared
    control["selection"] = selected
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            player.send("lesson.control", dict(action="play", sentence_id=None))
            old = player.audio()[-1]
            player.ack(old, "playback.started")
            player.question()
            events = player.audio()
            answer = events[-1]
            approved = next(event["payload"] for event in events if event["type"] == "sentence.approved")
            if selected:
                assert approved["visual_target"]["part_id"] == "coil"
            else:
                assert approved["visual_target"] is None
            gemini_call = next(r for r in calls if r.url.path.endswith(":generateContent"))
            prompt = json.loads(json.loads(gemini_call.content)["contents"][0]["parts"][0]["text"])
            assert prompt["teaching_context"]["confirmed_sentence_id"] == lesson.sentences[0].sentence_id
            assert prompt["teaching_context"]["visual_context"]["part_id"] == "case"
            completed = player.read()
            assert completed["type"] == "turn.completed"
            assert completed["payload"]["outcome"] == outcome
            assert completed["payload"]["restart_sentence_id"] == lesson.sentences[0].sentence_id
            player.ack(old)  # late lesson completion must not skip interrupted segment
            assert player.read()["payload"]["code"] == "stale_generation"
            assert client.get(f"/dev/lecture/sessions/{sid}").json()["cursor"]["revision"] == 0
            player.ack(answer, "playback.started")
            player.ack(answer)
            resumed = player.audio()[-1]
            assert resumed["payload"]["sentence_id"] == old["payload"]["sentence_id"]
            assert resumed["payload"]["stream_id"] != old["payload"]["stream_id"]
            player.ack(resumed, "playback.started")
            ack = player.ack(resumed)
            player.socket.send_json(ack)  # duplicate event is idempotent
            assert player.audio()[-1]["payload"]["sentence_id"] == lesson.sentences[1].sentence_id
            assert client.get(f"/dev/lecture/sessions/{sid}").json()["cursor"]["revision"] == 1
    assert len(calls) == 3


@pytest.mark.parametrize("playing", [False, True])
def test_typed_question_skips_stt_and_preserves_playback_intent(prepared, playing):
    pipeline, lesson, directory, calls, _ = prepared
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            if playing:
                player.send("lesson.control", dict(action="play", sentence_id=None))
                player.ack(player.audio()[-1], "playback.started")
            turn = uuid4().hex
            player.send("question.text", dict(text="What does the coil do?"), turn_id=turn)
            events = player.audio()
            assert events[0]["type"] == "turn.started"
            assert events[0]["turn_id"] == turn
            assert all(event["type"] != "transcript.final" for event in events)
            approved = next(event["payload"] for event in events if event["type"] == "sentence.approved")
            assert approved["visual_target"]["view_id"] == "cutaway"
            assert approved["visual_target"]["part_id"] == "coil"
            assert player.read()["payload"]["outcome"] == "answered"
            player.ack(events[-1], "playback.started")
            player.ack(events[-1])
            if playing:
                resumed = player.audio()[-1]
                assert resumed["payload"]["sentence_id"] == lesson.sentences[0].sentence_id
            else:
                snapshot = player.read()
                assert snapshot["type"] == "session.state"
                assert snapshot["payload"]["paused"]
            assert client.get(f"/dev/lecture/sessions/{sid}").json()["cursor"]["revision"] == 0
    assert len(calls) == 2
    assert not any(request.url.path.endswith("speech-to-text") for request in calls)


def test_provider_failure_preserves_cursor_and_requires_resume(prepared):
    pipeline, lesson, directory, _, control = prepared
    control["fail"] = True
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            player.question()
            assert player.read()["type"] == "turn.started"
            error = player.read()
            assert error["type"] == "error"
            assert "private" not in json.dumps(error)
            assert player.read()["payload"]["outcome"] == "failed"
            state = client.get(f"/dev/lecture/sessions/{sid}").json()
            assert state["paused"] and state["cursor"]["revision"] == 0
            player.send("lesson.control", dict(action="play", sentence_id=None))
            assert player.audio()[-1]["payload"]["sentence_id"] == lesson.sentences[0].sentence_id


def test_reconnect_and_restart_keep_position(prepared):
    pipeline, lesson, directory, _, _ = prepared
    app = create_app(pipeline, lecture_directory=directory)
    with TestClient(app) as client:
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            old_epoch = player.epoch
            player.send("lesson.control", dict(action="play", sentence_id=None))
            end = player.audio()[-1]
            player.ack(end, "playback.started")
            player.ack(end)
            player.audio()
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            assert player.epoch != old_epoch
            assert player.state["paused"]
            assert player.state["cursor"]["restart_sentence_id"] == lesson.sentences[1].sentence_id
            player.send("lesson.control", dict(action="play", sentence_id=None),
                        connection_epoch=old_epoch)
            assert player.read()["payload"]["code"] == "stale_connection"
            player.send("lesson.control", dict(action="play", sentence_id=None))
            assert player.audio()[-1]["payload"]["sentence_id"] == lesson.sentences[1].sentence_id


def test_malformed_audio_does_not_reach_provider(prepared):
    pipeline, _, directory, calls, _ = prepared
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            player.send("speech.start", {"playback_sentence_id": None}, turn_id="bad")
            player.read()
            player.send("audio.input", dict(stream_id="input", chunk_sequence=1, data="AAA="))
            assert player.read()["payload"]["code"] == "invalid_event"
            assert client.get(f"/dev/lecture/sessions/{sid}").json()["paused"]
    assert not calls


def test_repeated_speech_starts_and_interrupted_answer(prepared):
    pipeline, lesson, directory, calls, _ = prepared
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            player.send("speech.start", {"playback_sentence_id": None}, turn_id="first")
            player.question("latest")
            first = player.read()
            assert first["type"] == "turn.started" and first["turn_id"] == "first"
            answer = player.audio()[-1]
            assert answer["turn_id"] == "latest"
            player.read()  # turn.completed
            player.ack(answer, "playback.started")
            player.question("followup")
            replacement = player.audio()[-1]
            assert replacement["turn_id"] == "followup"
            player.read()
            player.ack(answer)
            assert player.read()["payload"]["code"] == "stale_generation"
            player.ack(replacement, "playback.started")
            player.ack(replacement)
            assert player.audio()[-1]["payload"]["sentence_id"] == lesson.sentences[0].sentence_id
    assert len(calls) == 6  # no provider request for the superseded first speech start


def test_replacement_socket_fences_old_connection(prepared):
    pipeline, _, directory, _, _ = prepared
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as old_ws:
            old = Player(old_ws)
            old.send("lesson.control", dict(action="play", sentence_id=None))
            old.audio()
            with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as new_ws:
                new = Player(new_ws)
                assert new.epoch != old.epoch
                assert new.state["paused"]
                new.send("lesson.control", dict(action="play", sentence_id=None))
                end = new.audio()[-1]
                new.ack(end)  # no playback.started: must not advance
                assert new.read()["payload"]["code"] == "playback_not_started"
                new.ack(end, "playback.started")
                new.ack(end)
                new.audio()
                assert client.get(f"/dev/lecture/sessions/{sid}").json()["cursor"]["revision"] == 1


def test_pending_provider_cancelled_on_disconnect(prepared):
    from rahbar.engine.sessions import LectureConnection

    pipeline, lesson, directory, _, _ = prepared
    async def run():
        entered, stopped = asyncio.Event(), asyncio.Event()
        class WaitingSTT:
            async def transcribe_file(self, audio, context):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
        class Socket:
            async def send_json(self, event):
                pass
        pipeline.stt = WaitingSTT()
        store = SessionStore(directory / "cancel.sqlite3")
        state = store.create(lesson, True)
        connection = LectureConnection(Socket(), state, store, lesson, directory, pipeline)
        ctx = await connection.start("waiting", listening=True)
        from rahbar.engine.audio import pcm_to_wav
        connection.task = asyncio.create_task(connection.answer(
            pcm_to_wav(b"\x01\x00" * 1600, 16000), ctx))
        await asyncio.wait_for(entered.wait(), 1)
        await connection.close()
        assert stopped.is_set()
        assert store.get(state.session_id).paused
        assert store.get(state.session_id).cursor.revision == 0
    asyncio.run(run())


def test_unprepared_lecture_returns_503(tmp_path):
    from test_pipeline import make_pipeline
    with TestClient(create_app(make_pipeline(lambda _: pytest.fail("provider called")),
                               lecture_directory=tmp_path)) as client:
        assert client.get("/dev/lecture").status_code == 503
        assert client.post("/dev/lecture/sessions", json=SELECTION).status_code == 503


@pytest.mark.parametrize("fault", ["oversized", "wrong_stream", "wrong_end"])
def test_input_boundaries_preserve_cursor(prepared, fault):
    pipeline, _, directory, calls, _ = prepared
    with TestClient(create_app(pipeline, lecture_directory=directory)) as client:
        sid = session(client)
        with client.websocket_connect(f"/dev/lecture/sessions/{sid}/events") as ws:
            player = Player(ws)
            player.send("speech.start", {"playback_sentence_id": None}, turn_id="input")
            player.read()
            if fault == "oversized":
                for i in range(17):
                    player.send("audio.input", dict(stream_id="input", chunk_sequence=i,
                                data=base64.b64encode(b"\0\0" * 30000).decode()))
            else:
                player.send("audio.input", dict(stream_id="input", chunk_sequence=0, data="AAA="))
                if fault == "wrong_stream":
                    player.send("audio.input", dict(stream_id="other", chunk_sequence=1, data="AAA="))
                else:
                    player.send("speech.end", dict(last_chunk_sequence=2))
            assert player.read()["payload"]["code"] == "invalid_event"
            state = client.get(f"/dev/lecture/sessions/{sid}").json()
            assert state["paused"] and state["cursor"]["revision"] == 0
    assert not calls


def test_stalled_audio_transport_pauses_without_advancing(prepared):
    from rahbar.engine.sessions import LectureConnection

    pipeline, lesson, directory, _, _ = prepared
    async def run():
        class Socket:
            async def send_json(self, event):
                raise TimeoutError
        store = SessionStore(directory / "slow.sqlite3")
        state = store.create(lesson, True)
        connection = LectureConnection(Socket(), state, store, lesson, directory, pipeline)
        with pytest.raises(asyncio.CancelledError):
            await connection.start("turn", listening=False)
        assert store.get(state.session_id).paused
        assert store.get(state.session_id).cursor.revision == 0
    asyncio.run(run())
