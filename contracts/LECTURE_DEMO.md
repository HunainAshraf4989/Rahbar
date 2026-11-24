# Lecture API

Protocol: `rahbar.v1`. Content: `relay-demo-v1`.

The development API serves a prepared relay lesson and accepts spoken and typed questions.
It uses ElevenLabs batch STT/TTS and Gemini passage selection. Answers are limited
to the supplied lesson passages; unsupported questions produce a spoken refusal.
Lesson text and assets are marked `draft` pending content review.

## Supabase runtime

With `DATABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL` (or `SUPABASE_URL`), and
`SUPABASE_SECRET_KEY` configured, the API automatically uses Supabase PostgreSQL
for sessions and private Storage for the imported lesson. The central loader reads
`.env` and `.env.local` from the working directory; `[YOUR-PASSWORD]` in the URI is
replaced with the encoded `SUPABASE_DB_PASSWORD`. Use the dashboard's Session pooler
URI for IPv4 connectivity. The publishable key is not needed by the backend.

Install the project dependencies, then run from the project directory containing
these configuration files:

```bash
PYTHONPATH=app/server python -m uvicorn rahbar.api:create_app --factory --host 127.0.0.1 --port 8000
npm --prefix app/web run dev
```

`GET /health/live` reports `storage_backend: "supabase"`. A partial cloud
configuration or unavailable database fails startup instead of silently using
SQLite. Without any Supabase configuration, local development continues to use
the prepared local lesson and SQLite.

The imported course manifest identifies the private content bundle. Startup
downloads missing/corrupt files into `.cache/supabase-runtime`, verifies SHA-256,
and uses this cache for audio playback and the existing `/dev/assets` routes.
Backend credentials and private Storage URLs are never sent to the browser.
Session cursor, playback intent, generation and connection epoch persist in the
existing `rahbar` tables. Stale generation/epoch/cursor writes are rejected;
database operations run off the event loop. Reconnect restores a paused session.

This remains the single-worker demo gateway. New sessions use anonymous demo
principals; account authentication and multi-worker coordination are not provided
by this integration. Existing imported demo sessions are supported.

PostgreSQL integration tests require an empty disposable database named
`rahbar_runtime_test` and `RAHBAR_TEST_POSTGRES_DSN` pointing to it. The test creates
and removes its own `rahbar` schema; never point it at a shared database.

## Local prepared-lesson setup

Run from the repository root with the project dependencies installed:

```sh
PYTHONPATH=app/server python tools/prepare_lecture.py
RAHBAR_ANSWER_MODE=gemini PYTHONPATH=app/server python -m uvicorn rahbar.api:create_app --factory --host 127.0.0.1 --port 8000
```

Configuration is loaded by `rahbar.config.load_settings`. Required credentials
are `ELEVENLABS_API_KEY` and `GEMINI_API_KEY`; the voice is selected through
`ELEVENLABS_VOICE_ID`. The loader also accepts the `RAHBAR_` aliases. Credentials
remain on the server.

Use a single worker. These `/dev` endpoints have no account authentication and
must remain local. A frontend development proxy can forward `/dev` HTTP requests
and WebSocket connections to port 8000.

## Content and audio

`content/relay/lesson-source.json` contains the ordered narration segments and
their corpus file, topic, row, source URL, excerpt and transcript hash. Factual
passages are separate from visual navigation cues. `education_data` is read-only.

`tools/prepare_lecture.py` writes `.cache/relay-lecture/lesson.json`, one WAV per
segment and checksum files. Audio cache keys include speech text, normalizer
version, provider, voice, model, language and sample rate. Repeated preparation
reuses valid clips; changed inputs or corrupted files trigger regeneration.
Playback never calls TTS for a prepared lesson segment.

Public types are defined in `app/web/src/api/contracts.generated.ts`; schemas are
in `contracts/contracts.schema.json`. Use `PreparedLesson`, `AssetManifest`,
`SessionCreate`, `SessionState` and the existing event models.

Each narration segment uses the protocol's `sentence_id` as its playback unit.
Segments can contain multiple sentences. Resume restarts that segment from its
beginning; word-level timing and sample-position resume are not supported.

## HTTP endpoints

| Method | Path | Result |
|---|---|---|
| GET | `/health/live` | Process status; no paid provider probe |
| GET | `/dev/lecture` | `PreparedLesson` |
| GET | `/dev/lecture/asset` | `AssetManifest` |
| GET | `/dev/assets/{filename}` | An image or SVG listed in the asset manifest |
| GET | `/dev/lesson-audio/{hash}.wav` | A WAV listed in the current lesson manifest |
| POST | `/dev/lecture/sessions` | `SessionCreate` → `SessionState`, status 201 |
| GET | `/dev/lecture/sessions/{session_id}` | Current `SessionState` |
| WS | `/dev/lecture/sessions/{session_id}/events` | Bidirectional JSON events |

Session creation request:

```json
{"course_id":"relay-demo","content_version":"relay-demo-v1","unit_id":"relay","lesson_id":"relay-demo","protocol":"rahbar.v1","visuals":true}
```

Save the returned `session_id` for reconnect. POST creates a separate session;
it does not resume an existing session. Session/cursor metadata is stored in
`.cache/relay-lecture/sessions.sqlite3`. Learner recordings and transcripts are
not persisted there. Account ownership and cross-session learner progress are
outside this API.

Responses under `/dev/lecture` include `X-Content-Review: draft` and
`Cache-Control: no-store`. HTTP errors use `{"detail":"code"}`:

| Status | Meaning |
|---|---|
| 404 | Unknown session, asset or audio file |
| 409 | Requested lesson selection or saved session lesson version does not match |
| 422 | Invalid request schema |
| 503 | `lecture_not_prepared`; run the preparation command |

Malformed or outdated prepared artifacts fail startup instead of serving stale
narration. Regenerate them with `tools/prepare_lecture.py`.

## Audio and visual playback

- Input: mono PCM16 little-endian, **16000 Hz**, base64 encoded in `audio.input`.
  Do not include a WAV header. Use approximately 100 ms chunks, contiguous chunk
  sequence numbers starting at zero, at most 300 chunks and 30 seconds per turn.
- Output: mono PCM16 little-endian, **24000 Hz**, carried in `audio.chunk`.
  The saved HTTP assets are WAV containers around the same PCM.
- Use `audio.path` from the lesson manifest; filenames are opaque. The WebSocket
  sends cached lesson PCM, so do not also play the HTTP file for the same turn.
- Render the registered PNG/SVG pair at identical dimensions. The current
  relay views use a 640 × 512 coordinate system.
- Validate the complete asset/version/view/frame/part target. Static frames use
  null. Activate highlighting on actual playback start and clear it on end,
  pause, interruption, error or disconnect.

## Event envelope

```json
{"protocol":"rahbar.v1","type":"speech.start","event_id":"unique-event","session_id":"session-id","connection_epoch":"socket-epoch","generation":2,"turn_id":"new-turn","sequence":8,"payload":{"playback_sentence_id":"relay-coil"}}
```

Event IDs are unique. Sequences increase per connection and direction. The first
socket event is `session.state`; use its epoch and generation, which supersede
the HTTP snapshot. Clients choose a new turn ID for every `speech.start`.
The server assigns generations and lesson turn IDs. Stream IDs identify audio
instances, not lesson positions.

## Playback and question sequence

1. Fetch the lesson and asset. Create a session and connect its WebSocket.
   Wait for the initial paused `session.state`.
2. Send `lesson.control` with `{"action":"play","sentence_id":null}`.
   Receive `turn.started`, `sentence.approved`, `audio.start`, ordered
   `audio.chunk` events and `audio.end`.
3. Send `playback.started` when audio actually begins and `playback.ended` after
   its final sample. The payload for both is:

   ```json
   {"sentence_id":"relay-purpose","stream_id":"server-stream-id","client_monotonic_ms":123.0,"interrupted_sample":null}
   ```

   Use the output's turn and generation. A valid completion acknowledgment
   advances the durable cursor once and starts the next saved segment.
4. On local VAD onset, immediately stop playback, empty queued audio and clear
   highlighting. Allocate a new pending turn and send `speech.start` with the
   current playback sentence ID, or null when idle. Do not wait for a server
   acknowledgment to stop local audio.
5. Send `audio.input` for that pending turn:

   ```json
   {"stream_id":"input-stream-id","chunk_sequence":0,"data":"base64-pcm"}
   ```

   Input may follow speech start before `turn.started` is received. The last
   known generation is a hint during this input phase; the accepted turn ID is
   authoritative. End capture with `speech.end`, payload
   `{"last_chunk_sequence":N}`.
6. The server transcribes the utterance, assembles the question, trusted lesson
   context and source passages for Gemini, validates the selected passage and
   synthesizes the answer. Receive `transcript.final`, `sentence.approved`,
   `audio.start/chunk/end`, then `turn.completed` with outcome and restart ID.
   Batch recognition produces no partial transcripts.
7. Acknowledge answer playback using `playback.started/ended`. The server resumes
   the interrupted lesson segment only after the answer's playback completion.
   `turn.completed` indicates generation completion, not playback completion.
   Interrupting an answer preserves the original lesson restart position.
8. After the final lesson segment is acknowledged, the session is paused and
   `cursor.restart_sentence_id` is null. Narration completion is independent of
   quiz, viva or unit-completion results.

## Typed questions

Send `question.text` with a fresh turn ID and `{"text":"What does the coil do?"}`.
Text must contain 1–1000 characters after trimming. Stop local playback when
sending the question. The gateway fences it as a new turn and uses the same
grounded answer and audio events as a spoken question. Typed input skips speech
recognition, so it produces no `transcript.final` event. The frontend can show
the submitted text immediately. An answer backed by one matching lesson passage
can reuse that passage's exact visual target during playback.

When the question interrupts narration, acknowledge the answer's playback to
resume the interrupted segment. When the lesson was paused or already complete,
the gateway sends a paused snapshot after the answer instead.

## State and failure handling

Accept playback output only for the current epoch, turn and assigned generation.
While a newer local speech turn is pending, block older playback even if it arrives
before the new acknowledgment. An earlier `turn.started` cannot enable playback
after a later speech start.

Duplicate event IDs have no repeated effect. Stale sequences, superseded epochs,
incorrect streams and old playback acknowledgments cannot advance the cursor.
Answer playback never advances the lesson cursor.

`lesson.control pause`, `playback.interrupted` and `turn.cancel` stop current work
and leave the lesson paused. `speech.start` also cancels the previous work.
`lesson.control play` resumes the cursor segment. `repeat` replays the cursor
segment, or the final segment if the lesson is complete; arbitrary seeking is
not supported.

A replacement connection receives a new epoch and a paused authoritative snapshot.
Clear transient client queues. `session.resume` with `{"cursor_revision":N}`
requests another snapshot; it does not seek or start playback. Resume with play.

Provider failures leave the cursor unchanged and the session paused. Display the
safe `error` message and allow retry or resume. Missing audio must not be marked
played. Errors never expose provider response bodies or credentials.

Supported events: session resume, lesson controls, typed questions, speech start/end, audio input,
turn cancel and playback acknowledgments. Other catalog events return an error.
Viva and authentication are not implemented by these endpoints.

## Volatile lesson conversation

Delivered question/answer pairs live in process memory, scoped by session, content
release and lesson. The latest four pairs are supplied to subsequent turns. They
are not part of persisted `SessionState`, SQLite rows or PostgreSQL session rows.
A playback-ended acknowledgement after playback-started records the answer;
cancelled, failed and interrupted replies do not become delivered context.

A brief reconnect to the same session retains that context. Thirty minutes of
inactivity expires it; a server restart also clears it. The single-worker gateway
owns this memory. Multiple workers require session affinity or a separately chosen
volatile store before deployment; this implementation does not promise cross-worker
continuity.

`DELETE /dev/lecture/sessions/{session_id}/conversation` closes the active
connection and clears the session's in-memory conversation. It leaves existing
lesson cursor/progress metadata intact. These remain local development routes,
not authenticated production endpoints. Full-corpus follow-up planning and spoken
clarification still require integration with the shared answer service.
