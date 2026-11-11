import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowLeft, ArrowRight, ArrowUp, AudioLines, ChevronRight, CircleCheck,
  Languages, LoaderCircle, MessageCircle, Mic, MicOff, Pause, Play, RotateCcw, TriangleAlert,
} from 'lucide-react';
import '@fontsource-variable/dm-sans';
import '@fontsource/noto-naskh-arabic/400.css';
import '@fontsource/noto-naskh-arabic/700.css';
import relayPreview from '../../../asset_gen/out/relay_4pin_cutaway.png';
import { Diagram } from './Diagram';
import { copy } from './copy';
import type { Language } from './copy';
import { canAskText, initialPlayerState, maxQuestionLength, RelayPlayer } from './relay';
import './styles.css';

function MixedText({ text }: { text: string }) {
  const hasUrdu = /[\u0600-\u06ff]/.test(text);
  return <p className="mixed-text" lang={hasUrdu ? 'ur' : 'en'} dir={hasUrdu ? 'rtl' : 'ltr'}>
    {text.split(/([A-Za-z0-9]+(?:[-' ][A-Za-z0-9]+)*)/).map((part, index) =>
      index % 2 ? <bdi lang="en" dir="ltr" key={index}>{part}</bdi> : part)}
  </p>;
}

function Course({ language }: { language: Language }) {
  const t = copy[language];
  const Forward = language === 'ur' ? ArrowLeft : ArrowRight;
  return <main className="shell course-page" id="main">
    <div className="page-heading">
      <h1>{t.course}</h1>
      <p>{t.courseDescription}</p>
    </div>
    <article className="course-card">
      <div className="course-copy">
        <div className="lesson-kind"><AudioLines size={19} aria-hidden="true" />{t.voice}</div>
        <h2>{t.title}</h2>
        <p>{t.lessonDescription}</p>
        <a href="#lesson" className="button primary">{t.open}<Forward size={18} aria-hidden="true" /></a>
      </div>
      <div className="course-art">
        <img src={relayPreview} alt={t.shortTitle} />
      </div>
    </article>
  </main>;
}

function Lesson({ language }: { language: Language }) {
  const t = copy[language];
  const [state, setState] = useState(initialPlayerState);
  const [question, setQuestion] = useState('');
  const player = useRef<RelayPlayer | null>(null);
  const thread = useRef<HTMLDivElement>(null);
  const followThread = useRef(true);
  const Back = language === 'ur' ? ArrowRight : ArrowLeft;

  useEffect(() => {
    const client = new RelayPlayer(setState);
    player.current = client;
    void client.connect();
    return () => {
      player.current = null;
      client.close();
    };
  }, []);

  const { lesson, phase, session, active, messages } = state;
  const activeId = phase === 'lesson' ? active?.sentence_id : undefined;
  const total = lesson?.sentences.length ?? 0;
  const current = (lesson?.sentences.findIndex(item => item.sentence_id === activeId) ?? -1) + 1;
  const completed = phase === 'complete' ? total : Math.max(
    state.completed ?? 0, (lesson?.sentences.findIndex(item => item.sentence_id === session?.cursor.last_completed_sentence_id) ?? -1) + 1,
  );
  const position = current || Math.min(completed + 1, total);
  const isPlaying = ['lesson', 'answer', 'loading', 'listening', 'thinking'].includes(phase);
  const isComplete = phase === 'complete';
  const canPlay = phase === 'ready' || isComplete || isPlaying;
  const canUseMic = phase !== 'connecting' && phase !== 'disconnected' && !isComplete;
  const canWrite = phase !== 'connecting' && phase !== 'disconnected';
  const PlayIcon = isPlaying ? Pause : isComplete ? RotateCcw : Play;
  const controlLabel = isPlaying ? t.pause : isComplete ? t.restart : completed ? t.resume : t.play;
  const MicIcon = state.microphone === 'on' ? Mic : state.microphone === 'starting' ? LoaderCircle : MicOff;
  const micLabel = state.microphone === 'on' ? t.micOff : state.microphone === 'starting' ? t.micStarting : t.micOn;

  useEffect(() => {
    const log = thread.current;
    if (log && (followThread.current || messages.length === 0)) {
      log.scrollTop = log.scrollHeight;
    }
  }, [messages]);

  function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (player.current?.askText(question)) {
      followThread.current = true;
      setQuestion('');
    }
  }

  return <main className="shell lesson-page" id="main">
    <nav className="breadcrumb" aria-label={t.courses}>
      <a href="#course" aria-label={t.back}><Back size={16} aria-hidden="true" />{t.course}</a>
      <ChevronRight size={14} className="breadcrumb-chevron" aria-hidden="true" />
      <span>{t.shortTitle}</span>
    </nav>
    <div className="lesson-heading page-heading">
      <div><h1>{t.title}</h1><p>{t.lessonDescription}</p></div>
      <span className="draft">{t.draft}</span>
    </div>

    {state.issue && <div className="notice" role="alert">
      <TriangleAlert size={19} aria-hidden="true" />
      <span>{t.issues[state.issue]}</span>
      {phase === 'disconnected' && <button className="button secondary" onClick={() => void player.current?.connect()}>{t.reconnect}</button>}
    </div>}

    <div className="lesson-grid">
      <Diagram asset={state.asset} active={active?.visual_target} language={language} />
      <section className="panel tutor-chat" aria-labelledby="chat-title">
        <div className="panel-heading">
          <h2 id="chat-title"><MessageCircle size={19} aria-hidden="true" />{t.chat}</h2>
          <span className="player-status" role="status" data-phase={phase}>
            {isComplete ? <CircleCheck size={17} aria-hidden="true" /> : <span className="status-dot" />}
            {t.status[phase]}
          </span>
        </div>

        <div className="chat-thread" role="log" aria-labelledby="chat-title" tabIndex={0} ref={thread}
          onScroll={event => {
            const log = event.currentTarget;
            followThread.current = log.scrollHeight - log.scrollTop - log.clientHeight < 64;
          }}>
          {messages.length === 0 ? <div className="chat-empty">
            <span className="chat-empty-icon"><AudioLines size={23} aria-hidden="true" /></span>
            <p>{t.chatEmpty}</p>
          </div> : <ol className="chat-messages">
            {messages.map(message => <li key={message.id} data-kind={message.kind}
              aria-current={message.id === state.playingMessageId ? 'true' : undefined}>
              <span className="message-label">
                {message.kind === 'question' ? t.yourQuestion : message.kind === 'answer' ? t.tutorAnswer : t.lessonMessage}
                {message.id === state.playingMessageId && <AudioLines size={14} aria-hidden="true" />}
              </span>
              <div className="message-bubble"><MixedText text={message.text} /></div>
            </li>)}
          </ol>}
        </div>

        <div className="chat-footer">
          {total > 0 && <div className="progress-row">
            <progress value={completed} max={total} aria-label={t.progress} />
            <span>{t.position} <bdi>{position}</bdi> {t.of} <bdi>{total}</bdi></span>
          </div>}
          <div className="player-buttons" aria-label={t.voice} role="group">
            <button className="button primary" disabled={!canPlay}
              onClick={() => {
                if (isPlaying) player.current?.pause();
                else if (isComplete) void player.current?.connect('restart');
                else player.current?.play();
              }}>
              <PlayIcon size={17} aria-hidden="true" />{controlLabel}
            </button>
            <button className="button secondary" disabled={!canPlay}
              onClick={() => {
                if (isComplete) player.current?.play();
                else void player.current?.connect('restart');
              }}>
              {!isComplete && <RotateCcw size={16} aria-hidden="true" />}
              {isComplete ? t.repeat : t.restart}
            </button>
          </div>
          <form className="chat-composer" onSubmit={submitQuestion}>
            <input dir="auto" aria-label={t.ask} placeholder={t.ask} maxLength={maxQuestionLength}
              value={question} onChange={event => setQuestion(event.target.value)} disabled={!canWrite} />
            <button type="button" className="button secondary icon-button" title={micLabel} aria-label={micLabel}
              disabled={!canUseMic && state.microphone === 'off'} aria-pressed={state.microphone === 'on'}
              onClick={() => void player.current?.toggleMic()}>
              <MicIcon size={19} className={state.microphone === 'starting' ? 'spinner' : undefined} aria-hidden="true" />
            </button>
            <button type="submit" className="button primary icon-button" title={t.send} aria-label={t.send}
              disabled={!canAskText(phase) || !question.trim()}>
              <ArrowUp size={20} aria-hidden="true" />
            </button>
          </form>
          {canUseMic && <p className="mic-hint">{state.microphone === 'on' ? t.micReady : t.micHint}</p>}
        </div>
      </section>
    </div>
  </main>;
}

function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem('rahbar.language') === 'ur' ? 'ur' : 'en');
  const [isLesson, setLesson] = useState(location.hash === '#lesson');
  const t = copy[language];

  useEffect(() => {
    const navigate = () => {
      setLesson(location.hash === '#lesson');
      window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);

  useEffect(() => {
    document.documentElement.lang = language;
    document.documentElement.dir = language === 'ur' ? 'rtl' : 'ltr';
    localStorage.setItem('rahbar.language', language);
  }, [language]);

  useEffect(() => {
    document.title = (isLesson ? t.title : t.course) + ' · ' + t.brand;
  }, [isLesson, t]);

  useEffect(() => {
    const heading = document.querySelector('main h1');
    if (heading instanceof HTMLElement && !heading.contains(document.activeElement)) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }, [isLesson]);

  return <>
    <header className="site-header">
      <div className="shell header-inner">
        <a href="#course" className="brand"><span className="brand-mark"><AudioLines size={22} aria-hidden="true" /></span>{t.brand}</a>
        <button className="language button secondary" onClick={() => setLanguage(language === 'en' ? 'ur' : 'en')}>
          <Languages size={18} aria-hidden="true" />
          <span lang={language === 'en' ? 'ur' : 'en'}>{t.alternate}</span>
        </button>
      </div>
    </header>
    {isLesson ? <Lesson language={language} /> : <Course language={language} />}
  </>;
}

createRoot(document.getElementById('root')!).render(<App />);
