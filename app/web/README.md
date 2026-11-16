# Rahbar web

This React app is the local Auto Electrician course and relay lesson player.
It uses the existing relay HTTP and WebSocket API.

## Run locally

From this directory with Node.js 22 installed:

```powershell
npm.cmd ci
npm.cmd run dev
```

Open the URL printed by Vite. It forwards HTTP and WebSocket requests under
`/dev` to `http://127.0.0.1:8000`. To use a different local address, set
`RAHBAR_WEB_API` before starting Vite.

Start the relay service from the repository root using
[the lecture API instructions](../../contracts/LECTURE_DEMO.md).
On Windows PowerShell, set `$env:PYTHONUTF8 = '1'` before preparing or serving
the UTF-8 lesson content, and set `$env:PYTHONPATH = 'app/server'` in the
same terminal. The prepared narration is stored in `.cache/relay-lecture`
at the repository root. Configure provider credentials through the backend's
existing settings.

If you received prepared audio from another machine and the service reports
`Prepared lesson is stale`, check the relay SVG line endings. The service hashes
the raw diagram bytes. A fresh Windows checkout created with
`git -c core.autocrlf=false clone <repo-url>` preserves the committed SVG bytes.

Open the relay lesson from **My Courses** and choose **Play lesson**.
Narration, submitted questions, and answers appear in the conversation.
Reconnect loads the saved position and waits for you to press play.

**Restart lesson** starts a fresh session and plays from the first segment.
After completing the lesson, **Replay last segment** is also available.

## Production build check

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run preview
```

The player tests use mocked browser APIs to check speech during audio loading,
late narration events, and pause handling without calling speech providers.

The preview uses the same local API proxy. The build and locally served VAD
runtime files are written to the ignored `.cache` directory.

## Platform preview

The dashboard requires an authenticated account. The lesson quiz list opens
each quiz separately. Quiz results and practice progress use browser session
storage for now.

For accounts, install `app/server/requirements-platform.txt` in the server
environment and use the additive `rahbar.platform_api:create_platform_app`
Uvicorn factory. Set `RAHBAR_DATABASE_URL` in the ignored repository-root
`.env.local` to a PostgreSQL database with the Rahbar schema applied. Set
`RAHBAR_WEB_ORIGIN` to the exact frontend origin if it differs from
`http://127.0.0.1:4173`. The auth module uses scrypt, revocable HTTP-only
cookies, origin checks, and local rate limits. Review email verification,
password recovery, shared throttling, and the deployment password blocklist
before opening the service to external users.

Use the chat composer to type a question or select its microphone to ask aloud.
The microphone pauses narration, and a short pause sends the recording through
the existing speech pipeline. The composer shows when the microphone is
listening, when it detects speech, and when the tutor is preparing a reply.
After the reply, choose **Continue lesson** to resume from the existing cursor.
Advancement to the next segment requires backend support.

Select the username in the sidebar to open profile settings. Changing a password
requires the current password and revokes previous sessions. New passwords need
eight or more characters, an uppercase letter, and a special character.

Drag the diagram or use the rotation buttons while the tutor speaks. Each
rendered angle uses its matching outline, so a part is highlighted only when
visible from that angle.
