import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowLeft, ArrowRight, AudioLines, BookOpen, ChevronRight, CircleCheck,
  Languages, LoaderCircle, Mic, MicOff, Pause, Play, RotateCcw, TriangleAlert,
} from 'lucide-react';
import '@fontsource-variable/dm-sans';
import '@fontsource/noto-naskh-arabic/400.css';
import '@fontsource/noto-naskh-arabic/700.css';
import relayPreview from '../../../asset_gen/out/relay_4pin_cutaway.png';
import { Diagram } from './Diagram';
import { copy } from './copy';
import type { Language } from './copy';
import { RelayPlayer } from './relay';
import type { PlayerState } from './relay';
import './styles.css';

function MixedText({ text }: { text: string }) {
  return <p className="mixed-text" lang="ur" dir="rtl">
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
  const [state, setState] = useState<PlayerState>({ phase: 'connecting', microphone: 'off' });
  const player = useRef<RelayPlayer | null>(null);
  const transcript = useRef<HTMLOListElement>(null);
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

  const { lesson, phase, session, active } = state;
  const activeId = phase === 'lesson' ? active?.sentence_id : undefined;
  const total = lesson?.sentences.length ?? 0;
  const current = (lesson?.sentences.findIndex(item => item.sentence_id === activeId) ?? -1) + 1;
  const completed = phase === 'complete' ? total : Math.max(
    state.completed ?? 0, (lesson?.sentences.findIndex(item => item.sentence_id === session?.cursor.last_completed_sentence_id) ?? -1) + 1,
  );
  const position = current || Math.min(completed + 1, total);
  const isPlaying = ['lesson', 'answer', 'loading', 'listening', 'thinking'].includes(phase);
  const canPlay = phase === 'ready' || phase === 'complete' || isPlaying;
  const isComplete = phase === 'complete';
  const canUseMic = phase !== 'connecting' && phase !== 'disconnected' && phase !== 'complete';
  const PlayIcon = isPlaying ? Pause : isComplete ? RotateCcw : Play;
  const controlLabel = isPlaying ? t.pause : isComplete ? t.restart : completed ? t.resume : t.play;
  const MicIcon = state.microphone === 'on' ? Mic : state.microphone === 'starting' ? LoaderCircle : MicOff;

  useEffect(() => {
    const container = transcript.current;
    const row = container?.querySelector<HTMLElement>('[aria-current="true"]');
    if (!container || !row) return;
    const parent = container.getBoundingClientRect();
    const child = row.getBoundingClientRect();
    if (child.top < parent.top || child.bottom > parent.bottom) {
      container.scrollTop += child.top - parent.top;
    }
  }, [activeId]);

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

    <section className="panel controls" aria-label={t.voice}>
      <div className="controls-top">
        <div className="player-buttons">
          <button className="button primary" disabled={!canPlay}
            onClick={() => {
              if (isPlaying) player.current?.pause();
              else if (isComplete) void player.current?.connect('restart');
              else player.current?.play();
            }}>
            <PlayIcon size={18} aria-hidden="true" />{controlLabel}
          </button>
          <button className="button secondary" disabled={!canPlay}
            onClick={() => {
              if (isComplete) player.current?.play();
              else void player.current?.connect('restart');
            }}>
            {!isComplete && <RotateCcw size={18} aria-hidden="true" />}
            {isComplete ? t.repeat : t.restart}
          </button>
          <button className="button secondary" disabled={!canUseMic && state.microphone === 'off'}
            aria-pressed={state.microphone === 'on'}
            onClick={() => void player.current?.toggleMic()}>
            <MicIcon size={18} className={state.microphone === 'starting' ? 'spinner' : undefined} aria-hidden="true" />
            {state.microphone === 'on' ? t.micOff : state.microphone === 'starting' ? t.micStarting : t.micOn}
          </button>
        </div>
        <span className="player-status" role="status" data-phase={phase}>
          {phase === 'complete' ? <CircleCheck size={17} aria-hidden="true" /> : <span className="status-dot" />}
          {t.status[phase]}
        </span>
      </div>
      {total > 0 && <div className="progress-row">
        <progress value={completed} max={total} aria-label={t.transcript} />
        <span>{t.position} <bdi>{position}</bdi> {t.of} <bdi>{total}</bdi></span>
      </div>}
      {canUseMic && <p className="mic-hint">{state.microphone === 'on' ? t.micReady : t.micHint}</p>}
    </section>

    <div className="lesson-grid">
      <Diagram asset={state.asset} active={active?.visual_target} language={language} />
      <section className="panel transcript" aria-labelledby="transcript-title">
        <div className="panel-heading">
          <h2 id="transcript-title"><BookOpen size={18} aria-hidden="true" />{t.transcript}</h2>
          {total > 0 && <span className="segment-count">{total} {t.segments}</span>}
        </div>
        {lesson && <ol ref={transcript} className="transcript-list" tabIndex={0} aria-labelledby="transcript-title">
          {lesson.sentences.map((sentence, index) => <li key={sentence.sentence_id}
            aria-current={sentence.sentence_id === activeId ? 'true' : undefined}>
            <span className="segment-number" aria-hidden="true">
              {index < completed && sentence.sentence_id !== activeId ? <CircleCheck size={19} /> : String(index + 1).padStart(2, '0')}
            </span>
            <MixedText text={sentence.canonical_text} />
          </li>)}
        </ol>}
      </section>
    </div>

    {(state.question || state.answer) && <section className="panel conversation" aria-label={t.tutorAnswer}>
      {state.question && <div><h2>{t.yourQuestion}</h2><MixedText text={state.question} /></div>}
      {state.answer && <div><h2>{t.tutorAnswer}</h2><MixedText text={state.answer} /></div>}
    </section>}
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
