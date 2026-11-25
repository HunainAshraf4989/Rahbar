import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, X } from "lucide-react";
import type { Language } from "./copy";
import whatsappLogo from "./assets/whatsapp.svg";
import "./whatsapp-tutor.css";

// Public chat number only; never expose Meta credentials through Vite.
const configuredNumber = String(import.meta.env.VITE_RAHBAR_WHATSAPP_NUMBER ?? "15551888196").replace(/[\s()+.-]/g, "");
const number = /^[1-9]\d{6,14}$/.test(configuredNumber) ? configuredNumber : "15551888196";

export function WhatsAppTutor({ language }: { language: Language }) {
  const t = (en: string, ur: string) => language === "ur" ? ur : en;
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const href = `https://wa.me/${number}?text=${encodeURIComponent(t("What is a relay?", "ریلے کیا ہے؟"))}`;

  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        toggle.current?.focus();
      }
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="whatsapp-widget" ref={root}>
      {open && (
        <section className="whatsapp-popover" id="whatsapp-tutor-panel" aria-label={t("WhatsApp tutor", "واٹس ایپ ٹیوٹر")}>
          <h2>{t("WhatsApp tutor", "واٹس ایپ ٹیوٹر")}</h2>
          <p>{t("Ask about your relay lesson by text or voice.", "ریلے کے سبق کے بارے میں لکھ کر یا بول کر پوچھیں۔")}</p>
          <a href={href} target="_blank" rel="noopener noreferrer">
            {t("Open WhatsApp", "واٹس ایپ کھولیں")}<ArrowUpRight size={17} aria-hidden="true" />
          </a>
        </section>
      )}
      <button ref={toggle} type="button" className="whatsapp-toggle" onClick={() => setOpen(!open)}
        aria-expanded={open} aria-controls="whatsapp-tutor-panel"
        aria-label={open ? t("Close WhatsApp chat", "واٹس ایپ چیٹ بند کریں") : t("Chat on WhatsApp", "واٹس ایپ پر چیٹ کریں")}>
        {open ? <X size={24} aria-hidden="true" /> : <img className="whatsapp-symbol" src={whatsappLogo} width={29} height={29} alt="" aria-hidden="true" />}
      </button>
    </div>
  );
}
