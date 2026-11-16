import { useState } from 'react';
import type { FormEvent } from 'react';
import { AudioLines, BookOpen, ClipboardCheck, Eye, EyeOff, Mic } from 'lucide-react';
import type { Language } from './copy';
import type { Account } from './api/contracts.generated';

const questions = [
  {
    prompt: ['What is a relay?', 'ریلے کیا ہے؟'],
    options: [['An electrically controlled switch', 'بجلی سے کنٹرول ہونے والا سوئچ'], ['A battery', 'بیٹری'], ['A fuse', 'فیوز']],
    correct: 0,
  },
  {
    prompt: ['What does the coil produce when current flows through it?', 'کوائل میں کرنٹ گزرنے سے کیا پیدا ہوتا ہے؟'],
    options: [['A mechanical lock', 'مکینیکل لاک'], ['A magnetic field', 'مقناطیسی میدان'], ['Stored fuel', 'ذخیرہ شدہ ایندھن']],
    correct: 1,
  },
];

export function Platform({ page, language, onAccount }: {
  page: 'quizzes' | 'quiz' | 'dashboard' | 'login' | 'signup'; language: Language;
  onAccount: (account: Account | null) => void;
}) {
  const ur = language === 'ur';
  const t = (en: string, urdu: string) => ur ? urdu : en;
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const [score, setScore] = useState<number | null>(() => {
    const value = sessionStorage.getItem('rahbar.preview.quizScore');
    return value === null ? null : Number(value);
  });
  const [submitted, setSubmitted] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<number>();
  const [lessonProgress, setLessonProgress] = useState(() =>
    Number(sessionStorage.getItem('rahbar.preview.lessonProgress') ?? 0));
  const title = {
    quiz: t('Relay practice quiz', 'ریلے کی مشقی کوئز'),
    quizzes: t('Lesson quizzes', 'اسباق کی کوئز'),
    dashboard: t('Student dashboard', 'طالب علم کا ڈیش بورڈ'),
    login: t('Log in', 'لاگ ان'),
    signup: t('Sign up', 'اکاؤنٹ بنائیں'),
  }[page];
  const auth = page === 'login' || page === 'signup';

  async function authenticate(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(undefined);
    try {
      const response = await fetch('/api/v1/auth/' + (page === 'signup' ? 'register' : 'login'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      if (!response.ok) {
        setError(response.status);
        return;
      }
      const account = await response.json() as Account;
      if (sessionStorage.getItem('rahbar.preview.account') !== account.account_id) clearDemoProgress();
      sessionStorage.setItem('rahbar.preview.account', account.account_id);
      onAccount(account);
      setPassword('');
      location.hash = 'dashboard';
    } catch {
      setError(503);
    } finally {
      setPending(false);
    }
  }

  function clearDemoProgress() {
    sessionStorage.removeItem('rahbar.preview.account');
    sessionStorage.removeItem('rahbar.preview.quizScore');
    sessionStorage.removeItem('rahbar.preview.lessonProgress');
    sessionStorage.removeItem('rahbar.relay.session');
    setScore(null);
    setLessonProgress(0);
  }

  function grade(event: FormEvent) {
    event.preventDefault();
    if (Object.keys(answers).length !== questions.length) return;
    const result = questions.filter((question, index) => question.correct === answers[index]).length;
    sessionStorage.setItem('rahbar.preview.quizScore', String(result));
    setScore(result);
    setSubmitted(true);
  }

  return <main className={auth ? 'auth-page' : 'shell platform-page'} id="main">
    <div className="page-heading">
      <h1>{title}</h1>
      {(page === 'quiz' || page === 'dashboard') && <p>{t('Practice progress is kept in this browser session.',
        'مشقی پیش رفت اس براؤزر سیشن میں محفوظ ہے۔')}</p>}
      {auth && <p>{page === 'login' ? t('Welcome back to Rahbar.', 'رہبر میں دوبارہ خوش آمدید۔')
        : t('Create your Rahbar account.', 'اپنا رہبر اکاؤنٹ بنائیں۔')}</p>}
    </div>
    {page === 'quizzes' && <section className="panel quiz-entry">
      <span className="dashboard-hero-icon"><ClipboardCheck size={22} aria-hidden="true" /></span>
      <div>
        <h2>{t('Relay: purpose and operation', 'ریلے کا مقصد اور طریقۂ کار')}</h2>
        <p>{questions.length} {t('questions', 'سوالات')}</p>
      </div>
      <a className="button primary" href="#quiz/relay">{t('Open quiz', 'کوئز کھولیں')}</a>
    </section>}
    {page === 'dashboard' && <>
      <section className="dashboard-hero">
      <span className="dashboard-hero-icon"><BookOpen size={25} aria-hidden="true" /></span>
      <h2>{t('Auto Electrician', 'آٹو الیکٹریشن')}</h2>
      <p>{t('Relay: purpose and operation', 'ریلے کا مقصد اور طریقۂ کار')}</p>
      <div className="player-buttons">
        <a className="button primary" href="#course">{t('Open courses', 'کورسز کھولیں')}</a>
      </div>
      </section>
      <dl className="progress-items">
        <div className="panel"><AudioLines aria-hidden="true" /><dt>{t('Lesson segments heard', 'سبق کے سنے ہوئے حصے')}</dt><dd><bdi>{lessonProgress}</bdi></dd></div>
        <div className="panel"><ClipboardCheck aria-hidden="true" /><dt>{t('Practice quiz', 'مشقی کوئز')}</dt><dd>{score === null
          ? t('Not attempted', 'ابھی شروع نہیں کیا') : <bdi>{score} / {questions.length}</bdi>}</dd></div>
        <div className="panel"><Mic aria-hidden="true" /><dt>{t('Spoken assessment', 'زبانی جائزہ')}</dt><dd>{t('Pending', 'باقی ہے')}</dd></div>
      </dl>
    </>}
    {page === 'quiz' && <form className="platform-form quiz-form" onSubmit={grade}>
      {questions.map((question, index) => <fieldset className="panel platform-card" key={index}>
        <legend>{question.prompt[ur ? 1 : 0]}</legend>
        {question.options.map((option, choice) => <label className="quiz-option" key={choice}>
          <input type="radio" name={'question-' + index} required disabled={submitted}
            checked={answers[index] === choice}
            onChange={() => setAnswers(previous => ({ ...previous, [index]: choice }))} />
          {option[ur ? 1 : 0]}
        </label>)}
        {submitted && <p>{t('Correct answer', 'درست جواب')} · {question.options[question.correct][ur ? 1 : 0]}</p>}
      </fieldset>)}
      {submitted ? <div className="player-buttons" role="status">
        <strong>{t('Score', 'نتیجہ')} <bdi>{score} / {questions.length}</bdi></strong>
        <button className="button secondary" type="button" onClick={() => {
          setAnswers({});
          setSubmitted(false);
        }}>{t('Try again', 'دوبارہ کوشش کریں')}</button>
        <a className="button primary" href="#dashboard">{t('View dashboard', 'ڈیش بورڈ دیکھیں')}</a>
      </div> : <button className="button primary">{t('Check answers', 'جوابات چیک کریں')}</button>}
    </form>}
    {auth && <form className="auth-form"
      onSubmit={event => void authenticate(event)}>
      <label>{t('Email address', 'ای میل')}<input type="email" dir="ltr" required autoComplete="email" value={email}
        onChange={event => setEmail(event.target.value)} /></label>
      <label htmlFor="account-password">{t('Password', 'پاس ورڈ')}</label>
      <div className="password-field">
      <input id="account-password" type={showPassword ? 'text' : 'password'} required maxLength={128}
        minLength={page === 'signup' ? 8 : undefined}
        pattern={page === 'signup' ? '(?=.*\\p{Lu})(?=.*[^\\p{L}\\p{N}\\s]).{8,128}' : undefined}
        aria-describedby={page === 'signup' ? 'password-rules' : undefined} value={password}
        autoComplete={page === 'signup' ? 'new-password' : 'current-password'}
        onChange={event => setPassword(event.target.value)} />
      <button type="button" aria-label={showPassword ? t('Hide password', 'پاس ورڈ چھپائیں') : t('Show password', 'پاس ورڈ دکھائیں')}
        onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button>
      </div>
      {page === 'signup' && <p className="auth-hint" id="password-rules">{t(
        'At least 8 characters, including an uppercase letter and a special character.',
        'کم از کم ۸ حروف، جن میں ایک بڑا حرف اور ایک خاص علامت ہو۔',
      )}</p>}
      <button className="button primary auth-submit" disabled={pending}>
        {pending ? t('Please wait…', 'انتظار کریں…') : title}
      </button>
      <p className="auth-switch">{page === 'login' ? t('New to Rahbar?', 'رہبر پر نئے ہیں؟') : t('Already have an account?', 'پہلے سے اکاؤنٹ ہے؟')}{' '}
        <a href={page === 'login' ? '#signup' : '#login'}>{page === 'login' ? t('Sign up', 'اکاؤنٹ بنائیں') : t('Log in', 'لاگ ان')}</a>
      </p>
    </form>}
    {error && <p className="notice" role="alert">{error === 401
      ? t('Email or password is incorrect.', 'ای میل یا پاس ورڈ غلط ہے۔')
      : error === 429 ? t('Too many attempts. Try again in a minute.', 'بہت زیادہ کوششیں۔ ایک منٹ بعد دوبارہ کوشش کریں۔')
      : error === 400 ? t('Choose a less common password with 8 or more characters, one uppercase letter and one special character.',
        'کم از کم ۸ حروف، ایک بڑا حرف اور ایک خاص علامت والا غیر عام پاس ورڈ منتخب کریں۔')
      : error === 409 ? t('Unable to register this account.', 'یہ اکاؤنٹ نہیں بنایا جا سکا۔')
      : error === 422 ? t('Check the email and password fields.', 'ای میل اور پاس ورڈ چیک کریں۔')
      : t('Account service is unavailable. Try again later.', 'اکاؤنٹ سروس دستیاب نہیں۔ بعد میں کوشش کریں۔')}</p>}
  </main>;
}
