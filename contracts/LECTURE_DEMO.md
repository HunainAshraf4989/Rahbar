# Relay lecture integration contract

Contract revision: **relay-demo.1**, 10 November 2026.
Implementation branch: **feat/elevenlabs-pipeline**.
Wire protocol: existing **rahbar.v1**, no new event fields.

This is the agreed frontend/backend boundary for Arfa and Hunain to work in
parallel. The lecture/session endpoints below are being implemented; the existing
`POST /dev/voice-turn` already runs standalone questions. Endpoint readiness and
verification will be updated here when the implementation passes its tests.

## Owners and shared inputs

- Hunain: lesson preparation from `education_data`, provenance, narration bake,
  asset manifest, session/cursor persistence, cancellation, prompt assembly,
  ElevenLabs STT/TTS and Gemini integration, backend tests.
- Arfa: review the prepared lesson, React player, microphone/resampling/VAD,
  visual/text presentation, playback acknowledgments, reconnect/error UX and
  browser acceptance. Connect accounts/course navigation/progress separately.
- Public content and wire types already exist in
  `app/web/src/api/contracts.generated.ts`; validate incoming events against
  `contracts/contracts.schema.json` and the event catalog. Do not invent a second
  lesson/cursor/event representation.
- Lesson source: `content/relay/lesson-source.json`, seven Urdu-English narration
  segments with exact source file/topic/row, source URL, excerpt and transcript
  hash. The original corpus is read-only and is not pushed.
- Content and asset review status is **draft**. This conceptual relay demonstration
  contains no installation procedure, terminal assignment or numeric rating.
  It does not establish reviewed curriculum or complete-unit assessment acceptance.

## Run and HTTP endpoints

From repository root, with the existing project Python environment and private
configuration loaded only by `rahbar.config.load_settings`:

```sh
PYTHONPATH=app/server python tools/prepare_lecture.py
RAHBAR_ANSWER_MODE=gemini PYTHONPATH=app/server python -m uvicorn rahbar.api:create_app --factory --host 127.0.0.1 --port 8000
```

Use one worker. This is a local development API, without account authentication.
Do not deploy it as an authenticated LMS API. Arfa can use a local frontend dev
proxy for `/dev` and WebSockets; no provider keys belong in browser code.

| Method and path | Response / request |
|---|---|
| `GET /health/live` | Existing process status; not a paid provider readiness check |
| `GET /dev/lecture` | `PreparedLesson`; header `X-Content-Review: draft` |
| `GET /dev/lecture/asset` | `AssetManifest` with image/overlay URLs and exact permitted parts |
| `GET /dev/assets/{filename}` | Only images/overlays named in the manifest |
| `GET /dev/lesson-audio/{hash}.wav` | Saved WAV named by a current lesson segment's `audio.path` |
| `POST /dev/lecture/sessions` | Body `SessionCreate` below; response `SessionState` (201) |
| `GET /dev/lecture/sessions/{session_id}` | Durable cursor and current session state |
| `WS /dev/lecture/sessions/{session_id}/events` | Bidirectional existing `rahbar.v1` JSON events |

Session selection (no client-supplied passages, cursor or answers):

```json
{"course_id":"relay-demo","content_version":"relay-demo-v1","unit_id":"relay","lesson_id":"relay-demo","protocol":"rahbar.v1","visuals":true}
```

Missing prepared audio: HTTP 503 `lecture_not_prepared`. Unknown session/asset:
404. A session selection or saved lesson version that does not match the configured
lesson: 409. Malformed HTTP input: 422. No synthesis occurs on lesson GET or play.
Persist `session_id` for reconnect/reload; POST creates a new demonstration session.
This demo does not yet bind sessions to accounts or implement cross-session learner
progress. Do not use a POST to resume an existing session.

## Narration and assets

`tools/prepare_lecture.py` produces `.cache/relay-lecture/lesson.json`, seven WAVs
and checksum files. Each hash covers speech text, normalizer version, provider,
voice, model, language and sample rate. A repeat bake reuses valid clips; changing
text/voice/model invalidates affected clips. Raw cache files stay out of Git.
Arfa consumes `audio.path`; filenames are opaque. Do not synthesize lecture text
per learner. Saved audio and live answers are mono PCM16 at **24000 Hz**.

The server delivers lesson PCM through `audio.*` events using those same saved
WAVs. HTTP audio URLs additionally support inspection/preloading. Do not play both
the HTTP file and event stream. Use one player and the server-issued stream ID.

Reuse the ray-traced `asset_gen/out/relay_4pin_exterior` and
`relay_4pin_cutaway` PNG/SVG pairs. Coordinates are the SVG viewBox, 640 by 512.
Validate asset/version/view/frame/part against `AssetManifest`; static frame is
null. Render the image and registered SVG at the same size. Activate a highlight
on actual playback start, not receipt of `sentence.approved` or `audio.start`.
Clear it on playback end, pause, interruption, error or connection loss.

## Event envelope

All JSON events contain the existing fields:

```json
{"protocol":"rahbar.v1","type":"speech.start","event_id":"unique-client-event","session_id":"from-session-response","connection_epoch":"from-socket-snapshot","generation":0,"turn_id":"new-client-turn","sequence":1,"payload":{"playback_sentence_id":"relay-coil"}}
```

Use unique event IDs and monotonically increasing sequence numbers per connection
and direction. `turn_id` is mandatory for turn/audio/playback events; session
events can use null. The socket's initial `session.state` gives the authoritative
epoch, generation, cursor and capabilities. It supersedes the HTTP snapshot.
The client generates a fresh turn ID for each speech start; the server generates
lesson turn IDs and assigns every generation. A stream ID is not a sentence ID.

## Runnable client sequence

1. GET lesson and asset; POST a session; connect its WebSocket. Wait for
   `session.state` before sending events. A newly attached socket is paused.
2. Send `lesson.control`, payload `{"action":"play","sentence_id":null}`.
   Receive `turn.started`, `sentence.approved`, `audio.start`, ordered
   `audio.chunk` events, and `audio.end`. These use one turn/generation/stream.
3. Schedule the received PCM in Web Audio. Send `playback.started` only when
   playback actually starts. Send `playback.ended` only after the final sample
   plays. Both payloads are `{"sentence_id":"…","stream_id":"…",
   "client_monotonic_ms":123.0,"interrupted_sample":null}`. The server advances
   the durable cursor once and emits the next saved lesson segment automatically.
4. On VAD speech onset, **immediately stop/clear all local playback** and highlights.
   Allocate a new turn ID and set it as `pending_turn_id`. Send `speech.start`
   with the interrupted lesson/reply sentence ID (or null if nothing was playing).
   The prior generation is only a hint on speech start; repeated starts before
   acknowledgment are allowed. The server cancels old work and returns
   `turn.started` for the new turn and assigned generation.
5. Capture mono **16000 Hz PCM16 little-endian**, base64 encoded. Send
   `audio.input` with `{"stream_id":"unique-input-stream","chunk_sequence":0,
   "data":"base64-pcm"}` and subsequent contiguous chunk numbers. Approximately
   100 ms per chunk is appropriate. Input may follow speech start before its ack;
   associate it with the pending turn ID. Limit an utterance to 30 seconds.
   Send `speech.end` with `{"last_chunk_sequence":N}`. No WAV header in events.
6. The backend wraps input for ElevenLabs batch STT, assembles question + trusted
   teaching context + lesson passages for Gemini, checks the selected evidence,
   then synthesizes the answer with ElevenLabs. Receive `transcript.final`,
   `sentence.approved`, `audio.start/chunk/end`, then `turn.completed` with
   outcome and `restart_sentence_id`. Batch STT has no partial transcript events.
7. Play and acknowledge answer audio with the same playback events. **Receipt of
   `turn.completed` is not permission to resume before the answer finishes.**
   After the current answer's `playback.ended`, the server automatically starts
   the saved interrupted lesson segment from its beginning. Answer playback
   never advances the lesson cursor. Another interruption during the answer
   preserves that same lesson restart point.
8. After the last lesson segment's `playback.ended`, the cursor has no restart
   sentence and the session is paused. This means narration finished, not that
   quiz/viva or unit completion passed.

The lesson manifest currently uses paragraph-sized segments as the existing
protocol's `sentence_id` playback unit. Restart is at this unit boundary, not an
exact sample. No intra-segment word timing/highlighting is claimed.

## Cancellation, reconnect and failures

- Ignore output unless epoch, turn and assigned generation are current. Before
  the latest pending speech turn is acknowledged, block prior audio even if it
  arrives first. An earlier `turn.started` must never re-enable playback.
- `lesson.control` pause stops backend work; the client stops audio immediately.
  Play resumes the cursor segment; repeat replays the current cursor segment
  (or final segment when narration is complete). It is not arbitrary seeking.
- `playback.interrupted` cancels current playback without advancing the cursor.
  `speech.start` already performs this cancellation; sending an old interrupted
  ack afterward must not change the new turn.
- `turn.cancel` cancels the current turn and leaves the lesson paused. Provider
  failure also leaves it paused. Show the safe error and a Resume/Retry choice;
  do not mark missing answer audio as played. Resume uses `lesson.control play`.
- Duplicate event IDs do not repeat effects. Old sequences, old epochs,
  mismatched streams and superseded playback acknowledgments cannot advance
  the cursor. Unknown/unsupported events receive a recoverable error.
- Reconnect to the same session URL. The new socket gets a new epoch, cancels
  transient work and returns a paused authoritative snapshot. Clear local queues
  and wait for the learner to play. `session.resume` with the last known
  `cursor_revision` requests another snapshot; it does not seek or replay audio.
- Current demo supports lesson controls, speech/audio, cancellation, playback and
  session resume. Typed questions, viva and account endpoints are outside this
  increment even though their schemas exist in the shared catalog.
- Input is bounded to 960000 PCM bytes; reject missing/out-of-order chunks or a
  mismatched end sequence. Silent/invalid input and unavailable providers produce
  safe errors. Client disconnect cancels pending paid requests.

## What to share

Shared F1 schemas and TypeScript types are already in main through PR #1. Arfa
needs this contract first; she can use the existing `contracts/fixtures` now.
After backend verification, share the explicit implementation/content/test files
from `feat/elevenlabs-pipeline`, including this contract. Hunain performs commits
and pushes. Do not push `.env`, `.cache`, `education_data`, `planning`, `agents.md`
or `handoff.md`. Saved narration is a separate generated artifact: either run the
bake command with authorized private configuration or copy the generated lesson
audio directory privately. Serving audio only needs the saved files.

## Verification status

Existing pipeline baseline: 91 Python tests passed. Source/asset/cache tests:
4 passed, including all seven source excerpts/hashes, exact overlay visibility,
cache reuse, corruption recovery and voice/text invalidation. Session runtime and
browser acceptance are pending at this contract checkpoint.
