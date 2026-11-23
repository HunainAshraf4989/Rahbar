import { useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { ArrowRight, ArrowLeft, AudioLines, X } from "lucide-react";
import { Platform } from "./Platform";
import type { Language } from "./copy";
import type { Account } from "./api/contracts.generated";
import "./welcome.css";

export function BookWelcome({
  language,
  page,
  languageButton,
  onAccount,
}: {
  language: Language;
  page: "login" | "signup" | null;
  languageButton: ReactNode;
  onAccount: (account: Account | null) => void;
}) {
  const ur = language === "ur";
  const t = (en: string, translated: string) => (ur ? translated : en);
  const [revealing, setRevealing] = useState(false);
  const overlay = useRef<HTMLElement | null>(null);
  const [lifting, setLifting] = useState(false);
  const flight = useRef<Animation | null>(null);
  useEffect(
    () => () => {
      flight.current?.cancel();
      overlay.current?.remove();
    },
    [],
  );
  async function openBook(event: MouseEvent<HTMLAnchorElement>) {
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      event.button !== 0
    )
      return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    event.preventDefault();
    if (lifting) return;
    const book = document.querySelector<HTMLElement>(".closed-book");
    if (!book) {
      location.hash = "signup";
      return;
    }
    setLifting(true);
    const box = book.getBoundingClientRect();
    const clone = book.cloneNode(true) as HTMLElement;
    clone.removeAttribute("href");
    clone.removeAttribute("aria-label");
    clone.setAttribute("aria-hidden", "true");
    clone.classList.add("book-flight");
    Object.assign(clone.style, {
      position: "fixed",
      left: box.x + "px",
      top: box.y + "px",
      width: box.width + "px",
      height: box.height + "px",
      transform: "none",
      margin: "0",
      zIndex: "100",
      pointerEvents: "none",
      transformOrigin: "top left",
    });
    document.body.appendChild(clone);
    overlay.current = clone;
    book.style.visibility = "hidden";
    try {
      flight.current = clone.animate(
        [{ transform: "none" }, { transform: "translateY(-40px) scale(1.1)" }],
        {
          duration: 420,
          easing: "cubic-bezier(.22,.7,.22,1)",
          fill: "forwards",
        },
      );
      await flight.current.finished;
      setRevealing(true);
      location.hash = "signup";
      let target: HTMLElement | null = null;
      for (let frame = 0; frame < 20 && !target; frame++) {
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => resolve()),
        );
        target = document.querySelector<HTMLElement>(".book-form-page");
      }
      if (!target) return;
      const rect = target.getBoundingClientRect();
      const destination = `translate(${rect.x - box.x}px, ${rect.y - box.y}px) scale(${rect.width / box.width}, ${rect.height / box.height})`;
      flight.current = clone.animate(
        [
          { transform: "translateY(-40px) scale(1.1)" },
          { transform: destination },
        ],
        {
          duration: 420,
          easing: "cubic-bezier(.22,.7,.22,1)",
          fill: "forwards",
        },
      );
      await flight.current.finished;
      clone.style.perspective = "1600px";
      const cover = clone.querySelector<HTMLElement>(".book-cover");
      const pages = clone.querySelector<HTMLElement>(".book-pages");
      const ribbon = clone.querySelector<HTMLElement>(".book-ribbon");
      if (pages) pages.style.opacity = "0";
      if (ribbon) ribbon.style.opacity = "0";
      if (cover) {
        cover.style.transformOrigin = ur ? "right center" : "left center";
        cover.style.backfaceVisibility = "hidden";
        flight.current = cover.animate(
          [
            { transform: "rotateY(0)" },
            { transform: ur ? "rotateY(180deg)" : "rotateY(-180deg)" },
          ],
          {
            duration: 950,
            easing: "cubic-bezier(.4,.05,.2,1)",
            fill: "forwards",
          },
        );
        await flight.current.finished;
      }
    } catch {
      /* Navigation cancels an in-flight cover. */
    } finally {
      clone.remove();
      overlay.current = null;
      book.style.visibility = "";
      setLifting(false);
      setRevealing(false);
    }
  }
  const Forward = ur ? ArrowLeft : ArrowRight;
  return (
    <div className="welcome" data-lifting={lifting} data-revealing={revealing}>
      <header className="welcome-header">
        <a href="#welcome" className="brand">
          <span className="brand-mark">
            <AudioLines size={22} aria-hidden="true" />
          </span>
          Rahbar
        </a>
        <div className="welcome-header-actions">
          {languageButton}
          <a href="#login" className="welcome-login">
            {t("Log in", "لاگ ان")}
            <Forward size={16} aria-hidden="true" />
          </a>
        </div>
      </header>
      {!page ? (
        <main className="welcome-stage" id="main">
          <section className="welcome-intro">
            <p className="welcome-eyebrow">
              {t("A guide to what comes next", "اگلے قدم کے لیے آپ کا رہنما")}
            </p>
            <h1>
              {t(
                "Understanding starts with a little guidance.",
                "سمجھنے کا سفر، رہنمائی کے ساتھ۔",
              )}
            </h1>
            <p className="welcome-description">
              {t(
                "Learn how things work. Explore visual lessons, ask in Urdu or English, and take the next step with Rahbar by your side.",
                "تصویری اسباق سے سیکھیں، اردو یا انگریزی میں سوال پوچھیں، اور رہبر کے ساتھ اگلا قدم اٹھائیں۔",
              )}
            </p>
            <a href="#signup" className="welcome-cta" onClick={openBook}>
              {t("Open your next chapter", "اپنا اگلا باب کھولیں")}
              <Forward size={20} aria-hidden="true" />
            </a>
          </section>
          <div className="closed-book-stage">
            <a
              className="closed-book"
              onClick={openBook}
              href="#signup"
              aria-label={t(
                "Open Rahbar — create an account",
                "رہبر کھولیں — اکاؤنٹ بنائیں",
              )}
            >
              <span className="book-pages" aria-hidden="true" />
              <span className="book-cover">
                <strong>Rahbar</strong>
                <svg
                  className="cover-landscape"
                  viewBox="0 0 300 250"
                  aria-hidden="true"
                >
                  <path d="M-30 250C40 115 97 207 143 115S241 47 330 10M-30 270C43 137 111 227 161 135S251 70 330 35M-30 290C45 160 126 247 179 154S262 94 330 60M-30 310C48 181 139 267 197 174S273 117 330 85M-30 330C50 204 154 287 215 193S284 141 330 110" />
                </svg>
              </span>
              <span className="book-ribbon" aria-hidden="true" />
            </a>
            <a className="book-invitation" onClick={openBook} href="#signup">
              {t("Open", "کھولیں")}
              <Forward size={17} aria-hidden="true" />
            </a>
          </div>
        </main>
      ) : (
        <div className="open-book-stage">
          <div className="open-book">
            <section
              className="book-inside"
              aria-label={t("Welcome to Rahbar", "رہبر میں خوش آمدید")}
            >
              <span className="book-section-label">
                {t("Your learning companion", "سیکھنے میں آپ کا ساتھی")}
              </span>
              <div>
                <AudioLines size={34} aria-hidden="true" />
                <h2>
                  {t(
                    "Every question opens a new possibility.",
                    "ہر سوال، ایک نیا امکان۔",
                  )}
                </h2>
                <p>
                  {t(
                    "Your Urdu–English voice tutor. Here to help you understand, one lesson at a time.",
                    "آپ کا اردو اور انگریزی صوتی استاد۔ ہر سبق کے ساتھ بہتر سمجھنے میں مددگار۔",
                  )}
                </p>
              </div>
            </section>
            <section
              className="book-form-page"
              aria-label={t("Your account", "آپ کا اکاؤنٹ")}
            >
              <a
                className="book-close"
                href="#welcome"
                aria-label={t("Close book", "کتاب بند کریں")}
              >
                <X size={20} />
              </a>
              <nav
                className="book-auth-tabs"
                aria-label={t("Account options", "اکاؤنٹ کے اختیارات")}
              >
                <a
                  href="#signup"
                  aria-current={page === "signup" ? "page" : undefined}
                >
                  {t("Sign up", "اکاؤنٹ بنائیں")}
                </a>
                <a
                  href="#login"
                  aria-current={page === "login" ? "page" : undefined}
                >
                  {t("Log in", "لاگ ان")}
                </a>
              </nav>
              <div className="book-form-content" key={page}>
                <Platform
                  page={page}
                  language={language}
                  onAccount={onAccount}
                />
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
