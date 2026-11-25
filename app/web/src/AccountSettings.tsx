import { useState } from 'react';
import type { FormEvent } from 'react';
import { KeyRound, UserRound } from 'lucide-react';
import type { Account } from './api/contracts.generated';
import type { Language } from './copy';

export function AccountSettings({ account, language, onExpired }: {
  account: Account; language: Language; onExpired: () => void;
}) {
  const t = (en: string, ur: string) => language === 'ur' ? ur : en;
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<number>();
  const name = account.full_name ?? account.email.split('@')[0];
  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const current_password = data.get('current');
    const new_password = data.get('next');
    if (new_password !== data.get('confirmation')) {
      setStatus(422);
      return;
    }
    setPending(true);
    setStatus(undefined);
    try {
      const response = await fetch('/api/v1/auth/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password, new_password }),
      });
      if (response.status === 401) onExpired();
      if (response.ok) form.reset();
      setStatus(response.status);
    } catch {
      setStatus(503);
    } finally {
      setPending(false);
    }
  }

  return <main className="shell platform-page settings-page" id="main">
    <div className="page-heading"><h1>{t('Profile settings', 'پروفائل کی ترتیبات')}</h1></div>
    <section className="panel settings-identity" aria-label={t('Your profile', 'آپ کا پروفائل')}>
      <span className="dashboard-hero-icon"><UserRound size={24} aria-hidden="true" /></span>
      <div><h2 dir="auto">{name}</h2><p dir="auto">{account.email}</p>{account.phone_number && <p dir="ltr">{account.phone_number}</p>}</div>
    </section>
    <section className="panel settings-password">
      <h2><KeyRound size={20} aria-hidden="true" />{t('Change password', 'پاس ورڈ تبدیل کریں')}</h2>
      <form className="auth-form" onSubmit={event => void changePassword(event)}>
        <label>{t('Current password', 'موجودہ پاس ورڈ')}
          <input type="password" name="current" required maxLength={128} autoComplete="current-password" />
        </label>
        <label>{t('New password', 'نیا پاس ورڈ')}
          <input type="password" name="next" required minLength={8} maxLength={128}
            autoComplete="new-password" aria-describedby="new-password-rules" />
        </label>
        <p className="auth-hint" id="new-password-rules">{t(
          'At least 8 characters, including an uppercase letter and a special character.',
          'کم از کم ۸ حروف، جن میں ایک بڑا حرف اور ایک خاص علامت ہو۔',
        )}</p>
        <label>{t('Confirm new password', 'نئے پاس ورڈ کی تصدیق کریں')}
          <input type="password" name="confirmation" required maxLength={128} autoComplete="new-password" />
        </label>
        <button className="button primary" disabled={pending}>
          {pending ? t('Saving…', 'محفوظ ہو رہا ہے…') : t('Update password', 'پاس ورڈ اپ ڈیٹ کریں')}
        </button>
        {status === 204 && <p role="status">{t('Password updated.', 'پاس ورڈ اپ ڈیٹ ہو گیا۔')}</p>}
        {status && status !== 204 && <p role="alert" className="notice">{status === 403
          ? t('Current password is incorrect.', 'موجودہ پاس ورڈ غلط ہے۔')
          : status === 422 ? t('The new passwords do not match.', 'نئے پاس ورڈ یکساں نہیں ہیں۔')
          : status === 400 ? t('Choose a less common password that meets the requirements.', 'شرائط کے مطابق غیر عام پاس ورڈ منتخب کریں۔')
          : status === 429 ? t('Too many attempts. Try again shortly.', 'بہت زیادہ کوششیں۔ بعد میں دوبارہ کوشش کریں۔')
          : t('Could not update the password. Try again.', 'پاس ورڈ اپ ڈیٹ نہیں ہو سکا۔ دوبارہ کوشش کریں۔')}</p>}
      </form>
    </section>
  </main>;
}
