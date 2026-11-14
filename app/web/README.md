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

In the lesson, choose **Play lesson**. To ask aloud, enable the microphone
once, grant browser permission, then speak during playback. The browser
stops the current audio as soon as it detects speech. The server resumes the
interrupted segment after the answer finishes. Reconnect loads the server's
saved position and waits for you to press play.

Type a question in the chat composer to use the same tutor without the
microphone. Narration, submitted questions, and spoken answers appear in the
conversation. The infographic canvas follows the current audio. Chat history
stays in the open lesson and clears when you start a new lesson session.

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
