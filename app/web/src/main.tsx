import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowLeft, ArrowRight, ArrowUp, AudioLines, BookOpen, ChevronRight, CircleCheck, ClipboardCheck,
  Languages, LayoutDashboard, LoaderCircle, LogIn, LogOut, Menu, Mic, Pause, Play, RotateCcw, Square, TriangleAlert, UserRound,
} from 'lucide-react';
import '@fontsource-variable/dm-sans';
import '@fontsource/noto-naskh-arabic/400.css';
import '@fontsource/noto-naskh-arabic/700.css';
import relayPreview from '../../../asset_gen/out/relay_4pin_cutaway.png';
import { Diagram } from './Diagram';
import { Platform } from './Platform';
import { AccountSettings } from './AccountSettings';
import type { Account } from './api/contracts.generated';
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
      <h1>{language === 'ur' ? 'میرے کورسز' : 'My Courses'}</h1>
      <p>{t.courseDescription}</p>
    </div>
    <section className="course-section" aria-labelledby="course-title">
      <h2 id="course-title"><BookOpen size={20} aria-hidden="true" />{t.course}</h2>
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
    </section>
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
  useEffect(() => {
    if (!lesson || !session) return;
    const previous = Number(sessionStorage.getItem('rahbar.preview.lessonProgress') ?? 0);
    sessionStorage.setItem('rahbar.preview.lessonProgress', String(Math.max(previous, completed)));
  }, [completed, lesson, session]);
  const isPlaying = ['lesson', 'answer', 'loading', 'listening', 'thinking'].includes(phase);
  const isComplete = phase === 'complete';
  const canPlay = phase === 'ready' || isComplete || isPlaying;
  const PlayIcon = isPlaying ? Pause : isComplete ? RotateCcw : Play;
  const canContinue = phase === 'ready' && state.waitingToContinue;
  const controlLabel = isPlaying ? t.pause : isComplete ? t.restart : canContinue ? t.continueLesson : completed ? t.resume : t.play;
  const ur = language === 'ur';
  const listening = state.microphone !== 'off' || phase === 'listening';
  const processing = phase === 'thinking';
  const voiceLabel = listening ? (ur ? 'مائیک بند کریں' : 'Stop listening') : (ur ? 'بول کر سوال پوچھیں' : 'Ask by voice');
  const activity = processing ? (ur ? 'جواب تیار ہو رہا ہے…' : 'Preparing a response…')
    : state.microphone === 'starting' ? (ur ? 'مائیک شروع ہو رہا ہے…' : 'Starting microphone…')
    : phase === 'listening' ? (ur ? 'آواز مل گئی · سن رہا ہے' : 'Speech detected · listening')
    : listening ? (ur ? 'سن رہا ہے · اب بولیں' : 'Listening · speak now') : '';
  const ActivityIcon = processing || state.microphone === 'starting' ? LoaderCircle : AudioLines;
  const connectionIssue = state.issue === 'connection' || state.issue === 'preparation';

  useEffect(() => {
    const log = thread.current;
    if (log && (followThread.current || messages.length === 0)) {
      log.scrollTop = log.scrollHeight;
    }
  }, [messages]);

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

    {state.issue && connectionIssue && <div className="notice" role="alert">
      <TriangleAlert size={19} aria-hidden="true" />
      <span>{t.issues[state.issue]}</span>
      {phase === 'disconnected' && <button className="button secondary" onClick={() => void player.current?.connect()}>{t.reconnect}</button>}
    </div>}

    <div className="lesson-grid">
      <Diagram asset={state.asset} active={active?.visual_target} language={language} />
      <section className="panel tutor-chat" aria-labelledby="chat-title">
        <div className="panel-heading">
          <h2 id="chat-title"><span className="tutor-mark"><AudioLines size={17} aria-hidden="true" /></span>{t.chat}</h2>
          {!activity && <span className="player-status" role="status" data-phase={phase}>
            {isComplete ? <CircleCheck size={17} aria-hidden="true" /> : <span className="status-dot" />}
            {t.status[phase]}
          </span>}
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
          <div className="chat-actions">
            <div className="chat-playback" aria-label={t.voice} role="group">
            <button className={canContinue ? 'button primary' : 'button chat-play'} disabled={!canPlay || (listening && phase !== 'listening')}
              onClick={() => {
                if (isPlaying) {
                  if (listening) void player.current?.toggleMic(true);
                  if (phase !== 'listening') player.current?.pause();
                }
                else if (isComplete) void player.current?.connect('restart');
                else player.current?.play();
              }}>
              <PlayIcon size={17} aria-hidden="true" />{controlLabel}
            </button>
            <button className="button chat-restart" disabled={!canPlay || listening || processing}
              aria-label={isComplete ? t.repeat : t.restart} title={isComplete ? t.repeat : t.restart}
              onClick={() => {
                if (isComplete) player.current?.play();
                else void player.current?.connect('restart');
              }}>
              <RotateCcw size={17} aria-hidden="true" />
            </button>
            </div>
          </div>
          <div className="composer-activity" data-active={!!activity} role="status" aria-atomic="true">
            {activity && <><ActivityIcon size={17} aria-hidden="true"
              className={processing || state.microphone === 'starting' ? 'spinner' : 'voice-pulse'} />{activity}</>}
          </div>
          {state.issue && !connectionIssue && <div className="notice composer-notice" role="alert">
            <TriangleAlert size={16} aria-hidden="true" /><span>{t.issues[state.issue]}</span>
          </div>}
          <form className="lesson-composer" onSubmit={event => {
            event.preventDefault();
            if (!listening && player.current?.askText(question)) {
              followThread.current = true;
              setQuestion('');
            }
          }}>
            <input aria-label={t.ask} placeholder={ur ? 'اپنا سوال لکھیں…' : 'Write a question…'}
              dir="auto" maxLength={maxQuestionLength} value={question}
              onChange={event => setQuestion(event.target.value)} />
            <button type="button" className="button composer-mic" aria-label={voiceLabel} title={voiceLabel}
              aria-pressed={listening} disabled={!listening && !canAskText(phase)}
              onClick={() => {
                followThread.current = true;
                if (!listening && phase === 'lesson') player.current?.pause();
                void player.current?.toggleMic(true);
              }}>
              {listening ? <Square size={17} aria-hidden="true" /> : <Mic size={19} aria-hidden="true" />}
            </button>
            <button className="button primary composer-send" aria-label={t.send} title={t.send}
              disabled={listening || !canAskText(phase) || !question.trim()}><ArrowUp size={20} aria-hidden="true" /></button>
          </form>
        </div>
      </section>
    </div>
  </main>;
}

function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem('rahbar.language') === 'ur' ? 'ur' : 'en');
  const [route, setRoute] = useState(location.hash);
  const [account, setAccount] = useState<Account | null>(null);
  const [checkingAccount, setCheckingAccount] = useState(true);
  const [checkedRoute, setCheckedRoute] = useState<string>();
  const [accountUnavailable, setAccountUnavailable] = useState(false);
  const [sessionCheck, setSessionCheck] = useState(0);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutFailed, setLogoutFailed] = useState(false);
  const [expanded, setExpanded] = useState(() => !matchMedia('(max-width: 720px)').matches);
  const isLesson = route === '#lesson';
  const platform = route === '#quiz' ? 'quizzes' : route === '#quiz/relay' ? 'quiz' : route === '#dashboard' ? 'dashboard'
    : route === '#login' ? 'login' : route === '#signup' ? 'signup' : null;
  const auth = platform === 'login' || platform === 'signup';
  const profile = route === '#profile';
  const protectedPage = profile || platform === 'dashboard';
  const t = copy[language];
  const ur = language === 'ur';
  const links = [
    { path: '#dashboard', label: ur ? 'ڈیش بورڈ' : 'Dashboard', icon: LayoutDashboard },
    { path: '#course', label: ur ? 'میرے کورسز' : 'My Courses', icon: BookOpen },
    { path: '#quiz', label: ur ? 'اسباق کی کوئز' : 'Lesson quizzes', icon: ClipboardCheck },
  ];
  const selectedRoute = platform === 'quiz' ? '#quiz' : platform || profile ? route : '#course';

  useEffect(() => {
    const controller = new AbortController();
    setCheckingAccount(true);
    setAccountUnavailable(false);
    void fetch('/api/v1/me', { signal: controller.signal, cache: 'no-store' })
      .then(async response => {
        if (response.status === 401) {
          if (!controller.signal.aborted) setAccount(null);
          return;
        }
        if (!response.ok) throw new Error('Account service unavailable');
        const verified = await response.json() as Account;
        if (!controller.signal.aborted) setAccount(verified);
      })
      .catch(() => { if (!controller.signal.aborted) setAccountUnavailable(true); })
      .finally(() => {
        if (!controller.signal.aborted) {
          setCheckedRoute(route);
          setCheckingAccount(false);
        }
      });
    return () => controller.abort();
  }, [route, sessionCheck]);

  useEffect(() => {
    if (checkedRoute === route && !checkingAccount && !accountUnavailable && protectedPage && !account) location.replace('#login');
  }, [account, accountUnavailable, checkedRoute, checkingAccount, protectedPage, route]);

  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setLogoutFailed(false);
    try {
      const response = await fetch('/api/v1/auth/logout', { method: 'POST' });
      if (!response.ok) throw new Error('Logout failed');
      for (const key of ['rahbar.preview.account', 'rahbar.preview.quizScore',
        'rahbar.preview.lessonProgress', 'rahbar.relay.session']) sessionStorage.removeItem(key);
      setAccount(null);
      location.replace('#login');
    } catch {
      setLogoutFailed(true);
    } finally {
      setLoggingOut(false);
    }
  }

  useEffect(() => {
    const navigate = () => {
      setRoute(location.hash);
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
    const heading = document.querySelector('main h1');
    document.title = (heading?.textContent ?? t.course) + ' · ' + t.brand;
  }, [route, t, checkingAccount, checkedRoute]);

  useEffect(() => {
    const heading = document.querySelector('main h1');
    if (heading instanceof HTMLElement && !heading.contains(document.activeElement)) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }, [route]);

  const brand = <a href="#course" className="brand"><span className="brand-mark">
    <AudioLines size={21} aria-hidden="true" /></span>{t.brand}</a>;
  const languageButton = <button className="language button secondary" onClick={() => setLanguage(ur ? 'en' : 'ur')}>
    <Languages size={17} aria-hidden="true" />
    <span lang={ur ? 'en' : 'ur'}>{t.alternate}</span>
  </button>;
  const navLink = ({ path, label, icon: Icon }: typeof links[number]) => <a key={path} href={path}
      aria-label={label} title={label}
      onClick={() => { if (matchMedia('(max-width: 720px)').matches) setExpanded(false); }}
      aria-current={selectedRoute === path ? 'page' : undefined}>
      <Icon size={19} aria-hidden="true" /><span>{label}</span>
    </a>;
  const nav = <nav className="sidebar-nav" aria-label={ur ? 'صفحے' : 'Main navigation'}>
    {links.map(navLink)}
  </nav>;

  if (auth) return <div className="auth-layout">
    <div className="auth-cover">
      <div className="auth-cover-copy">
        <span className="auth-cover-mark"><AudioLines size={38} aria-hidden="true" /></span>
        <strong>{t.brand}</strong>
        <p>{ur ? 'آپ کا اردو اور انگریزی صوتی استاد' : 'Your Urdu–English voice tutor'}</p>
      </div>
    </div>
    <div className="auth-content">
      <header className="auth-header">
        <div className="brand"><span className="brand-mark"><AudioLines size={21} aria-hidden="true" /></span>{t.brand}</div>
        {languageButton}
      </header>
      <Platform key={platform} page={platform} language={language} onAccount={setAccount} />
    </div>
  </div>;

  const accountLabel = account?.email.split('@')[0] ?? (ur ? 'لاگ ان' : 'Log in');
  return <div className="workspace-layout" data-expanded={expanded}>
    <aside className="sidebar">
      <div className="sidebar-top">
        {brand}
        <button className="sidebar-toggle" type="button" aria-expanded={expanded}
          aria-label={expanded ? (ur ? 'مینو سمیٹیں' : 'Collapse navigation') : (ur ? 'مینو کھولیں' : 'Expand navigation')}
          onClick={() => setExpanded(value => !value)}><Menu size={22} aria-hidden="true" /></button>
      </div>
      {nav}
      <div className="sidebar-footer">
        <a className="account-link" href={account ? '#profile' : '#login'}
          aria-label={account?.email ?? accountLabel} title={account?.email ?? accountLabel}>
          {account ? <UserRound size={19} aria-hidden="true" /> : <LogIn size={19} aria-hidden="true" />}
          <span dir="auto">{accountLabel}</span>
        </a>
        {account && <button className="logout-icon" type="button" disabled={loggingOut}
          aria-label={ur ? 'لاگ آؤٹ' : 'Log out'} title={ur ? 'لاگ آؤٹ' : 'Log out'}
          onClick={() => void logout()}><LogOut size={19} aria-hidden="true" /></button>}
      </div>
    </aside>
    <div className="workspace-content">
      <header className="workspace-tools">{languageButton}</header>
      {logoutFailed && <p className="shell notice" role="alert">{ur ? 'لاگ آؤٹ نہیں ہو سکا۔ دوبارہ کوشش کریں۔' : 'Could not log out. Please try again.'}</p>}
      {protectedPage && (checkedRoute !== route || checkingAccount || accountUnavailable || !account)
        ? <main id="main" className="shell platform-page" role="status">{accountUnavailable ? <>
          <p>{ur ? 'اکاؤنٹ سروس دستیاب نہیں۔' : 'Account service is unavailable.'}</p>
          <button className="button secondary" onClick={() => setSessionCheck(value => value + 1)}>
            {ur ? 'دوبارہ کوشش کریں' : 'Try again'}
          </button>
        </> : ur ? 'اکاؤنٹ چیک ہو رہا ہے…' : 'Checking your account…'}</main>
        : profile && account ? <AccountSettings account={account} language={language} onExpired={() => {
          setAccount(null);
          location.replace('#login');
        }} />
        : isLesson ? <Lesson language={language} /> : platform
          ? <Platform key={platform} page={platform} language={language} onAccount={setAccount} />
          : <Course language={language} />}
    </div>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
