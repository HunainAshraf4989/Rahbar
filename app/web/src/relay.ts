import type {
  ApprovedSentence, AssetManifest, AudioChunkEvent, AudioEndEvent, AudioInputEvent,
  AudioStartEvent, ErrorEvent, LessonControlEvent, PlaybackEndedEvent, PlaybackStartedEvent,
  PreparedLesson, QuestionTextEvent, SentenceApprovedEvent, SessionCreate, SessionState, SessionStateEvent,
  SpeechEndEvent, SpeechStartEvent, TranscriptFinalEvent, TurnStartedEvent,
} from './api/contracts.generated';
import { encodePcm, pcmWave } from './audio';
import type { MicVAD } from '@ricky0123/vad-web';

type Incoming = SessionStateEvent | TurnStartedEvent | SentenceApprovedEvent | AudioStartEvent
  | AudioChunkEvent | AudioEndEvent | ErrorEvent | TranscriptFinalEvent;
type Outgoing = LessonControlEvent | PlaybackStartedEvent | PlaybackEndedEvent
  | SpeechStartEvent | SpeechEndEvent | AudioInputEvent | QuestionTextEvent;
type Payload<K extends Outgoing['type']> = Extract<Outgoing, {type: K}>['payload'];

export type Phase = 'connecting' | 'disconnected' | 'ready' | 'loading' | 'lesson'
  | 'listening' | 'thinking' | 'answer' | 'complete';
export type Issue = 'connection' | 'preparation' | 'playback' | 'provider' | 'microphone' | 'noSpeech' | 'tooLong';

export type ChatMessage = {
  id: string;
  kind: 'lesson' | 'question' | 'answer';
  text: string;
  segmentId?: string;
};

export interface PlayerState {
  phase: Phase;
  lesson?: PreparedLesson;
  asset?: AssetManifest;
  session?: SessionState;
  active?: ApprovedSentence;
  completed?: number;
  messages: ChatMessage[];
  playingMessageId?: string;
  issue?: Issue;
  microphone: 'off' | 'starting' | 'on';
}

const lessonPath = '/dev/lecture';
const sessionPath = lessonPath + '/sessions';
const storageKey = 'rahbar.relay.session';
export const maxQuestionLength = 1000;
export const initialPlayerState: PlayerState = { phase: 'connecting', microphone: 'off', messages: [] };

export function canAskText(phase: Phase): boolean {
  return phase === 'ready' || phase === 'lesson' || phase === 'complete';
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(String(response.status));
  return response.json() as Promise<T>;
}

export class RelayPlayer {
  private state = initialPlayerState;
  private messageSessionId?: string;
  private socket?: WebSocket;
  private abort?: AbortController;
  private media = new Audio();
  private mediaUrl?: string;
  private vad?: MicVAD;
  private microphoneStream?: MediaStream;
  private microphoneContext?: AudioContext;
  private micRequest = 0;
  private epoch = '';
  private generation = 0;
  private sequence = 0;
  private lastSequence = -1;
  private eventIds = new Set<string>();
  private turn?: string;
  private questionTurn?: string;
  private pendingTurn?: string;
  private questionTimer?: ReturnType<typeof setTimeout>;
  private approved?: ApprovedSentence;
  private playRequested = false;
  private pausePending = false;
  private pauseGeneration?: number;
  private stream?: {
    id: string; sentence: ApprovedSentence; chunks: Uint8Array<ArrayBuffer>[]; bytes: number;
  };

  constructor(private update: (state: PlayerState) => void) {}

  private set(patch: Partial<PlayerState>) {
    this.state = { ...this.state, ...patch };
    this.update(this.state);
  }

  async connect(mode: 'resume' | 'restart' = 'resume') {
    this.close();
    if (mode === 'restart') this.primeMedia();
    this.set({ phase: 'connecting', issue: undefined, session: undefined, completed: undefined });
    const abort = new AbortController();
    this.abort = abort;
    const timeout = setTimeout(() => abort.abort(), 10_000);
    try {
      const [lesson, asset] = await Promise.all([
        fetchJson<PreparedLesson>(lessonPath, { signal: abort.signal }),
        fetchJson<AssetManifest>(lessonPath + '/asset', { signal: abort.signal }),
      ]);
      if (this.abort !== abort) return;
      this.set({ lesson, asset });
      const saved = mode === 'resume' ? sessionStorage.getItem(storageKey) : null;
      let session: SessionState | undefined;
      if (saved) {
        try {
          session = await fetchJson<SessionState>(sessionPath + '/' + encodeURIComponent(saved), { signal: abort.signal });
          if (session.cursor.lesson_version !== lesson.lesson_version) session = undefined;
        } catch (error) {
          if (!(error instanceof Error) || !['404', '409'].includes(error.message)) throw error;
        }
      }
      if (!session) {
        const selection: SessionCreate = {
          course_id: 'relay-demo', unit_id: lesson.unit_id, lesson_id: lesson.lesson_id,
          content_version: lesson.content_version, protocol: 'rahbar.v1', visuals: true,
        };
        session = await fetchJson<SessionState>(sessionPath, {
          method: 'POST', signal: abort.signal,
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(selection),
        });
      }
      if (this.abort !== abort) return;
      const url = new URL(sessionPath + '/' + encodeURIComponent(session.session_id) + '/events', location.href);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(url);
      this.socket = socket;
      this.epoch = '';
      this.generation = 0;
      this.sequence = 0;
      this.lastSequence = -1;
      this.eventIds.clear();
      this.set({ session });
      const handshake = setTimeout(() => { if (!this.epoch) socket.close(); }, 8000);
      socket.onmessage = event => {
        if (this.socket !== socket) return;
        try {
          const first = !this.epoch;
          this.receive(JSON.parse(event.data) as Incoming);
          if (first && this.epoch) {
            if (this.messageSessionId !== session.session_id) this.set({ messages: [] });
            this.messageSessionId = session.session_id;
            sessionStorage.setItem(storageKey, session.session_id);
            if (mode === 'restart') this.startPlayback('play');
          }
        } catch {
          socket.close();
        }
      };
      socket.onclose = () => {
        clearTimeout(handshake);
        if (this.socket !== socket) return;
        this.close();
        this.set({ phase: 'disconnected', issue: 'connection' });
      };
    } catch (error) {
      if (this.abort === abort) {
        this.stopOutput();
        this.set({ phase: 'disconnected', issue: error instanceof Error && error.message === '503'
          ? 'preparation' : 'connection' });
      }
    } finally {
      clearTimeout(timeout);
    }
  }

  close() {
    this.abort?.abort();
    this.abort = undefined;
    const socket = this.socket;
    this.socket = undefined;
    socket?.close();
    this.stopOutput();
    this.resetTurn();
    this.stopMic();
    this.playRequested = false;
    this.pausePending = false;
    this.pauseGeneration = undefined;
  }

  private resetTurn() {
    clearTimeout(this.questionTimer);
    this.turn = this.pendingTurn = this.questionTurn = undefined;
  }

  private send<K extends Outgoing['type']>(type: K, payload: Payload<K>, turn?: string) {
    if (this.socket?.readyState !== WebSocket.OPEN || !this.epoch || !this.state.session) return;
    this.socket.send(JSON.stringify({
      protocol: 'rahbar.v1', event_id: crypto.randomUUID(), session_id: this.state.session.session_id,
      connection_epoch: this.epoch, generation: this.generation, turn_id: turn ?? null,
      sequence: this.sequence++, type, payload,
    }));
  }

  play() {
    if (this.state.phase !== 'ready' && this.state.phase !== 'complete') return;
    this.primeMedia();
    this.startPlayback(this.state.phase === 'complete' ? 'repeat' : 'play');
  }

  askText(input: string): boolean {
    const text = input.trim();
    if (!canAskText(this.state.phase) || !text || text.length > maxQuestionLength
      || this.socket?.readyState !== WebSocket.OPEN) return false;
    this.primeMedia();
    const turn = crypto.randomUUID();
    this.pendingTurn = this.questionTurn = turn;
    this.playRequested = true;
    this.set({
      phase: 'thinking', issue: undefined,
      messages: [...this.state.messages, { id: turn, kind: 'question', text }],
    });
    this.send('question.text', { text }, turn);
    return true;
  }

  private startPlayback(action: 'play' | 'repeat') {
    this.set({ phase: 'loading', issue: undefined });
    this.playRequested = true;
    this.send('lesson.control', { action, sentence_id: null });
  }

  private primeMedia() {
    // Prime the same media element during the user's gesture for later socket audio.
    this.stopOutput();
    this.mediaUrl = URL.createObjectURL(pcmWave([new Uint8Array(480)], 480));
    this.media.src = this.mediaUrl;
    void this.media.play().catch(() => {});
  }

  pause() {
    if (this.pausePending) return;
    this.stopOutput();
    this.playRequested = false;
    this.pausePending = true;
    this.set({ phase: 'loading' });
    this.sendPause();
  }

  private sendPause() {
    if (this.pauseGeneration === this.generation) return;
    this.pauseGeneration = this.generation;
    this.send('lesson.control', { action: 'pause', sentence_id: null });
  }

  private stopOutput() {
    this.media.onplaying = this.media.onended = this.media.onerror = null;
    this.media.pause();
    this.media.removeAttribute('src');
    this.media.load();
    if (this.mediaUrl) URL.revokeObjectURL(this.mediaUrl);
    this.mediaUrl = undefined;
    this.stream = this.approved = undefined;
    if (this.state.active || this.state.playingMessageId) {
      this.set({ active: undefined, playingMessageId: undefined });
    }
  }

  private receive(event: Incoming) {
    if (event.protocol !== 'rahbar.v1' || event.session_id !== this.state.session?.session_id
      || event.sequence <= this.lastSequence || (this.epoch && this.epoch !== event.connection_epoch)) return;
    if (event.type !== 'session.state' && !this.epoch) return;
    this.lastSequence = event.sequence;
    if (event.generation < this.generation || this.eventIds.has(event.event_id)) return;
    this.eventIds.add(event.event_id);
    if (this.eventIds.size > 4096) this.eventIds = new Set([event.event_id]);

    if (event.type === 'session.state') {
      this.epoch = event.connection_epoch;
      this.generation = event.generation;
      if (this.pendingTurn && !this.pausePending) return;
      this.stopOutput();
      this.resetTurn();
      this.pausePending = this.playRequested = false;
      this.pauseGeneration = undefined;
      this.set({ session: event.payload, completed: undefined, phase: event.payload.cursor.restart_sentence_id ? 'ready' : 'complete' });
      if (!event.payload.cursor.restart_sentence_id) this.stopMic();
      return;
    }

    if (event.type === 'turn.started') {
      if (event.generation <= this.generation) return;
      this.generation = event.generation;
      if (this.pausePending) {
        this.sendPause();
        return;
      }
      if (this.pendingTurn && event.turn_id !== this.pendingTurn) return;
      if (!this.playRequested && event.turn_id !== this.pendingTurn) return;
      this.stopOutput();
      this.turn = event.turn_id;
      if (this.pendingTurn === event.turn_id) this.pendingTurn = undefined;
      return;
    }

    if (event.type === 'error') {
      if (this.pausePending && event.payload.code === 'stale_generation') {
        this.generation = event.generation;
        this.sendPause();
        return;
      }
      if (this.pendingTurn || event.generation !== this.generation) return;
      this.stopOutput();
      this.resetTurn();
      this.pausePending = this.playRequested = false;
      this.pauseGeneration = undefined;
      this.set({ phase: this.state.session?.cursor.restart_sentence_id ? 'ready' : 'complete', issue: 'provider' });
      return;
    }
    if (this.pausePending || this.pendingTurn || !this.turn
      || event.turn_id !== this.turn || event.generation !== this.generation) return;

    switch (event.type) {
      case 'sentence.approved':
        this.approved = event.payload;
        break;
      case 'transcript.final':
        this.set({
          messages: [...this.state.messages, { id: event.event_id, kind: 'question', text: event.payload.canonical_text }],
        });
        break;
      case 'audio.start':
        if (event.payload.sample_rate === 24000 && event.payload.channels === 1
          && event.payload.encoding === 'pcm_s16le' && this.approved?.sentence_id === event.payload.sentence_id) {
          this.stream = { id: event.payload.stream_id, sentence: this.approved, chunks: [], bytes: 0 };
        }
        break;
      case 'audio.chunk': {
        const stream = this.stream;
        if (!stream || event.payload.stream_id !== stream.id || event.payload.sentence_id !== stream.sentence.sentence_id
          || event.payload.chunk_sequence !== stream.chunks.length) break;
        const bytes = Uint8Array.from(atob(event.payload.data), character => character.charCodeAt(0));
        if (!bytes.length || bytes.length % 2 || stream.bytes + bytes.length > 5_760_000) {
          this.failPlayback();
          break;
        }
        stream.chunks.push(bytes);
        stream.bytes += bytes.length;
        break;
      }
      case 'audio.end': {
        const stream = this.stream;
        if (stream && event.payload.stream_id !== stream.id) break;
        if (stream && event.payload.sentence_id === stream.sentence.sentence_id && stream.bytes
          && event.payload.last_chunk_sequence === stream.chunks.length - 1) {
          this.playStream(stream, this.turn);
        } else {
          this.failPlayback();
        }
        break;
      }
    }
  }

  private failPlayback() {
    this.pause();
    this.set({ issue: 'playback' });
  }

  private playStream(stream: NonNullable<RelayPlayer['stream']>, turn: string) {
    const isAnswer = turn === this.questionTurn;
    const url = URL.createObjectURL(pcmWave(stream.chunks, stream.bytes));
    const identity = { sentence_id: stream.sentence.sentence_id, stream_id: stream.id };
    this.stopOutput();
    this.mediaUrl = url;
    this.media.src = url;
    let started = false;
    this.media.onplaying = () => {
      if (this.mediaUrl !== url || started) return;
      started = true;
      const index = this.state.lesson?.sentences.findIndex(item => item.sentence_id === identity.sentence_id) ?? -1;
      const previous = this.state.messages.at(-1);
      const replaying = !isAnswer && previous?.kind === 'lesson' && previous.segmentId === identity.sentence_id;
      const message: ChatMessage = replaying ? previous : {
        id: stream.id,
        kind: isAnswer ? 'answer' : 'lesson',
        text: stream.sentence.canonical_text,
        segmentId: isAnswer ? undefined : identity.sentence_id,
      };
      this.set({
        ...(!isAnswer && index >= 0 ? { completed: index } : {}),
        phase: isAnswer ? 'answer' : 'lesson', active: stream.sentence,
        messages: replaying ? this.state.messages : [...this.state.messages, message],
        playingMessageId: message.id,
      });
      this.send('playback.started', { ...identity, client_monotonic_ms: performance.now(), interrupted_sample: null }, turn);
    };
    this.media.onended = () => {
      if (this.mediaUrl !== url || !started) return;
      this.stopOutput();
      this.set({ phase: 'loading' });
      this.send('playback.ended', { ...identity, client_monotonic_ms: performance.now(), interrupted_sample: null }, turn);
    };
    this.media.onerror = () => { if (this.mediaUrl === url) this.failPlayback(); };
    void this.media.play().catch(() => { if (this.mediaUrl === url) this.failPlayback(); });
  }

  async toggleMic() {
    if (this.state.microphone !== 'off') {
      this.stopMic();
      if (this.state.phase === 'listening') this.pause();
      return;
    }
    const request = ++this.micRequest;
    if (this.state.phase === 'ready') this.primeMedia();
    this.set({ microphone: 'starting', issue: undefined });
    let vad: MicVAD | undefined;
    try {
      const audioContext = new AudioContext();
      this.microphoneContext = audioContext;
      await audioContext.resume();
      const { MicVAD } = await import('@ricky0123/vad-web');
      if (request !== this.micRequest) return;
      vad = await MicVAD.new({
        model: 'v5', baseAssetPath: '/vad/', onnxWASMBasePath: '/vad/', startOnLoad: false,
        audioContext,
        getStream: async () => {
          const stream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          });
          if (request !== this.micRequest) {
            stream.getTracks().forEach(track => track.stop());
            throw new Error('Microphone request canceled');
          }
          this.microphoneStream = stream;
          return stream;
        },
        onSpeechStart: () => { if (request === this.micRequest) this.startQuestion(); },
        onSpeechEnd: audio => { if (request === this.micRequest) this.submitQuestion(audio); },
        onVADMisfire: () => { if (request === this.micRequest) this.cancelQuestion('noSpeech'); },
      });
      if (request !== this.micRequest) {
        await vad.destroy().catch(() => {});
        return;
      }
      await vad.start();
      if (request !== this.micRequest) {
        await vad.destroy().catch(() => {});
        return;
      }
      this.vad = vad;
      this.set({ microphone: 'on' });
    } catch {
      if (vad) void vad.destroy().catch(() => {});
      if (request === this.micRequest) {
        this.stopMic();
        this.set({ issue: 'microphone' });
      }
    }
  }

  private stopMic() {
    this.micRequest++;
    const vad = this.vad;
    this.vad = undefined;
    this.microphoneStream?.getTracks().forEach(track => track.stop());
    this.microphoneStream = undefined;
    const audioContext = this.microphoneContext;
    this.microphoneContext = undefined;
    if (vad) void vad.destroy().catch(() => {});
    if (audioContext) void audioContext.close().catch(() => {});
    if (this.state.microphone !== 'off') this.set({ microphone: 'off' });
  }

  private startQuestion() {
    if (this.pausePending) return;
    const awaitingPlayback = this.state.phase === 'loading' && this.playRequested;
    if (!awaitingPlayback && !['lesson', 'ready', 'answer', 'thinking'].includes(this.state.phase)) return;
    const interrupted = this.state.active?.sentence_id ?? null;
    this.stopOutput();
    const turn = crypto.randomUUID();
    this.pendingTurn = this.questionTurn = turn;
    this.playRequested = true;
    this.set({ phase: 'listening', issue: undefined });
    this.send('speech.start', { playback_sentence_id: interrupted }, turn);
    clearTimeout(this.questionTimer);
    this.questionTimer = setTimeout(() => {
      this.cancelQuestion('tooLong');
      this.stopMic();
    }, 29_000);
  }

  private submitQuestion(audio: Float32Array) {
    if (this.state.phase !== 'listening' || !this.questionTurn) return;
    clearTimeout(this.questionTimer);
    const stream_id = crypto.randomUUID();
    const limit = Math.min(audio.length, 480_000);
    if (limit === 0) return this.cancelQuestion('noSpeech');
    let chunk_sequence = 0;
    for (let offset = 0; offset < limit; offset += 1600) {
      const data = encodePcm(audio.subarray(offset, Math.min(offset + 1600, limit)));
      this.send('audio.input', { stream_id, chunk_sequence, data }, this.questionTurn);
      chunk_sequence++;
    }
    this.send('speech.end', { last_chunk_sequence: chunk_sequence - 1 }, this.questionTurn);
    this.set({ phase: 'thinking' });
  }

  private cancelQuestion(issue: Issue) {
    if (this.state.phase !== 'listening') return;
    clearTimeout(this.questionTimer);
    this.pause();
    this.set({ issue });
  }
}
