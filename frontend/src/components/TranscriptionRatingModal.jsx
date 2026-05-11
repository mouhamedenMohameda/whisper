import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { markRatingPromptHandled } from "../utils/ratingPromptSession.js";
import { submitTranscriptionJobRatings } from "../utils/api.js";

/**
 * @param {{
 *   open: boolean;
 *   jobPublicIds: string[];
 *   fileLabel?: string;
 *   onClose: () => void;
 * }} props
 */
export default function TranscriptionRatingModal({ open, jobPublicIds, fileLabel, onClose }) {
  const { t } = useTranslation();
  const [stars, setStars] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  const handleSkip = useCallback(() => {
    markRatingPromptHandled(jobPublicIds);
    setStars(0);
    onClose();
  }, [jobPublicIds, onClose]);

  const handleSubmit = useCallback(async () => {
    if (stars < 1 || stars > 5) return;
    setSubmitting(true);
    try {
      await submitTranscriptionJobRatings(jobPublicIds, stars);
      markRatingPromptHandled(jobPublicIds);
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", { detail: { msg: t("feedback.ratingThanks"), type: "success" } }),
      );
      setStars(0);
      onClose();
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: e?.message || t("feedback.ratingFail"), type: "error" },
        }),
      );
    } finally {
      setSubmitting(false);
    }
  }, [jobPublicIds, stars, onClose, t]);

  if (!open) return null;

  const label = typeof fileLabel === "string" && fileLabel.trim() ? fileLabel.trim() : t("common.audio");

  return (
    <div
      className="fixed inset-0 z-[240] flex items-end justify-center p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rating-modal-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/50 backdrop-blur-[2px]"
        aria-label={t("feedback.ratingCloseBackdrop")}
        onClick={handleSkip}
      />
      <div className="relative z-10 w-full max-w-md rounded-3xl border border-slate-200/90 bg-white p-6 shadow-soft-lg dark:border-slate-700 dark:bg-slate-900">
        <h2 id="rating-modal-title" className="font-display text-lg font-bold text-slate-900 dark:text-white">
          {t("feedback.ratingTitle")}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-400">
          {t("feedback.ratingSubtitle", { name: label })}
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2" role="group" aria-label={t("feedback.ratingStarsAria")}>
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => setStars(n)}
              className={`flex size-11 items-center justify-center rounded-2xl text-xl transition ${
                stars >= n
                  ? "bg-amber-400 text-amber-950 shadow-md ring-2 ring-amber-500/40 dark:bg-amber-500 dark:text-amber-950"
                  : "bg-slate-100 text-slate-400 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-500 dark:hover:bg-slate-700"
              }`}
              aria-pressed={stars === n}
              aria-label={t("feedback.ratingStarAria", { n })}
            >
              ★
            </button>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap gap-2 sm:justify-end">
          <button
            type="button"
            onClick={handleSkip}
            disabled={submitting}
            className="rounded-2xl border border-slate-200/90 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            {t("feedback.ratingLater")}
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={submitting || stars < 1}
            className="rounded-2xl bg-gradient-to-r from-brand-600 to-amber-600 px-4 py-2.5 text-sm font-bold text-white shadow-glow transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {submitting ? t("feedback.ratingSending") : t("feedback.ratingSend")}
          </button>
        </div>
      </div>
    </div>
  );
}
