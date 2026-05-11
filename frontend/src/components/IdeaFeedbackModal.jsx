import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { submitAppFeedback } from "../utils/api.js";

/**
 * @param {{
 *   open: boolean;
 *   uiLocale: string;
 *   onClose: () => void;
 * }} props
 */
export default function IdeaFeedbackModal({ open, uiLocale, onClose }) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const close = useCallback(() => {
    setText("");
    onClose();
  }, [onClose]);

  const handleSubmit = useCallback(async () => {
    const msg = text.trim();
    if (!msg) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", { detail: { msg: t("feedback.ideasEmpty"), type: "info" } }),
      );
      return;
    }
    setSubmitting(true);
    try {
      await submitAppFeedback(msg, uiLocale);
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", { detail: { msg: t("feedback.ideasThanks"), type: "success" } }),
      );
      close();
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: e?.message || t("feedback.ideasFail"), type: "error" },
        }),
      );
    } finally {
      setSubmitting(false);
    }
  }, [close, text, t, uiLocale]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[240] flex items-end justify-center p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ideas-modal-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/50 backdrop-blur-[2px]"
        aria-label={t("feedback.ideasCloseBackdrop")}
        onClick={close}
      />
      <div className="relative z-10 flex max-h-[min(90dvh,560px)] w-full max-w-lg flex-col rounded-3xl border border-slate-200/90 bg-white p-6 shadow-soft-lg dark:border-slate-700 dark:bg-slate-900">
        <h2 id="ideas-modal-title" className="font-display text-lg font-bold text-slate-900 dark:text-white">
          {t("feedback.ideasTitle")}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{t("feedback.ideasHint")}</p>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={8000}
          rows={8}
          className="mt-4 min-h-[140px] w-full resize-y rounded-2xl border border-slate-200/90 bg-white/90 px-3 py-2.5 text-sm text-slate-900 shadow-inner outline-none ring-brand-500/0 transition focus:border-brand-400 focus:ring-2 focus:ring-brand-500/25 dark:border-slate-600 dark:bg-slate-950/80 dark:text-slate-100"
          placeholder={t("feedback.ideasPlaceholder")}
          dir="auto"
        />
        <div className="mt-2 text-end text-[10px] text-slate-400">{text.length} / 8000</div>
        <div className="mt-4 flex flex-wrap gap-2 sm:justify-end">
          <button
            type="button"
            onClick={close}
            disabled={submitting}
            className="rounded-2xl border border-slate-200/90 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            {t("feedback.ideasCancel")}
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={submitting}
            className="rounded-2xl bg-gradient-to-r from-brand-600 to-amber-600 px-4 py-2.5 text-sm font-bold text-white shadow-glow transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {submitting ? t("feedback.ideasSending") : t("feedback.ideasSend")}
          </button>
        </div>
      </div>
    </div>
  );
}
