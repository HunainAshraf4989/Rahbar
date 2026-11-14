import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { transformWithEsbuild } from 'vite';

// Exercise the real player without a microphone, network, or paid providers.
async function moduleUrl(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8');
  const { code } = await transformWithEsbuild(source, path, { loader: 'ts' });
  return 'data:text/javascript;base64,' + Buffer.from(code).toString('base64');
}
const audioUrl = await moduleUrl('../src/audio.ts');
const source = (await readFile(new URL('../src/relay.ts', import.meta.url), 'utf8'))
  .replace("'./audio'", JSON.stringify(audioUrl));
const { code } = await transformWithEsbuild(source, 'relay.ts', { loader: 'ts' });
const { RelayPlayer } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));

globalThis.Audio = class {
  pause() {}
  removeAttribute() {}
  load() {}
  play() { return Promise.resolve(); }
};
globalThis.WebSocket = { OPEN: 1 };

function setup(t) {
  const sent = [];
  const player = new RelayPlayer(() => {});
  player.socket = { readyState: 1, send: raw => sent.push(JSON.parse(raw)), close() {} };
  player.epoch = 'epoch';
  player.generation = 1;
  player.state = { phase: 'ready', microphone: 'on', messages: [], session: { session_id: 'session' } };
  t.after(() => player.close());
  return { player, sent };
}

for (const moment of ['initial play', 'between segments']) {
  test(`speech during ${moment} cancels pending playback and submits the question`, t => {
    const { player, sent } = setup(t);
    player.play();
    if (moment === 'between segments') {
      player.turn = 'old-turn';
      player.playStream({ id: 'old-stream', sentence: { sentence_id: 'sentence', canonical_text: 'Lesson' },
        chunks: [new Uint8Array(2)], bytes: 2 }, 'old-turn');
      player.media.onplaying();
      player.media.onended();
    }
    assert.equal(player.state.phase, 'loading');
    player.startQuestion();
    assert.equal(player.state.phase, 'listening');
    assert.equal(sent.at(-1).type, 'speech.start');
    const questionTurn = sent.at(-1).turn_id;
    // A queued narration turn must not replace the pending question.
    player.receive({ protocol: 'rahbar.v1', session_id: 'session', connection_epoch: 'epoch',
      sequence: 1, generation: 2, event_id: 'late-start', type: 'turn.started', turn_id: 'late-lesson', payload: {} });
    assert.equal(player.pendingTurn, questionTurn);
    assert.equal(player.state.phase, 'listening');
    player.receive({ protocol: 'rahbar.v1', session_id: 'session', connection_epoch: 'epoch',
      sequence: 2, generation: 3, event_id: 'question-start', type: 'turn.started', turn_id: questionTurn, payload: {} });
    player.submitQuestion(new Float32Array(1600));
    assert.equal(player.state.phase, 'thinking');
    assert.deepEqual(sent.slice(-2).map(event => event.type), ['audio.input', 'speech.end']);
    assert.ok(sent.slice(-2).every(event => event.turn_id === questionTurn && event.generation === 3));
  });
}

test('speech while a pause acknowledgment is pending does not cancel the pause', t => {
  const { player, sent } = setup(t);
  player.play();
  player.pause();
  const count = sent.length;
  player.startQuestion();
  assert.equal(player.state.phase, 'loading');
  assert.equal(sent.length, count);
  assert.equal(sent.at(-1).payload.action, 'pause');
});
