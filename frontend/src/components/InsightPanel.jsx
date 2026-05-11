import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useTranslation } from "react-i18next";
import { ENGINE_INSIGHT } from "../branding.js";
import { formatMru } from "../utils/usage.js";
import UsageDetailsToggle from "./UsageDetailsToggle.jsx";

const mdMini = {
  h2({ children }) {
    return <h4 className="mt-4 text-base font-bold text-slate-900 dark:text-slate-100">{children}</h4>;
  },
  h3({ children }) {
    return <h5 className="mt-3 text-sm font-semibold text-slate-900 dark:text-slate-50">{children}</h5>;
  },
  p({ children }) {
    return <p className="mb-3 text-sm leading-relaxed text-slate-700 dark:text-slate-300">{children}</p>;
  },
  ul({ children }) {
    return <ul className="mb-3 list-disc space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-300">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="mb-3 list-decimal space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-300">{children}</ol>;
  },
  li({ children }) {
    return <li className="leading-relaxed">{children}</li>;
  },
  strong({ children }) {
    return <strong className="font-semibold text-brand-700 dark:text-brand-300">{children}</strong>;
  },
  blockquote({ children }) {
    return (
      <blockquote className="border-l-4 border-brand-400/70 pl-3 text-sm italic text-slate-600 dark:border-brand-500/60 dark:text-slate-400">
        {children}
      </blockquote>
    );
  },
};

/** @param {{ subject: string, onSubjectChange: (v: string) => void, deepSummary: string, truncated?: boolean, usage?: Record<string, unknown>, show: boolean, onClose?: () => void }} props */
export default function InsightPanel({ subject, onSubjectChange, deepSummary, truncated, usage = {}, show, onClose }) {
  const { t } = useTranslation();
  const gp = Number(usage?.groqInsightPromptTokens ?? 0) || 0;
  const gc = Number(usage?.groqInsightCompletionTokens ?? 0) || 0;
  const insightOptMru = Number(usage?.groqInsightOptionalBilledMru ?? 0) || 0;

  if (!show) return null;

  return (
    <section className="glass-panel rounded-3xl border border-brand-200/50 bg-gradient-to-br from-brand-50/80 via-white to-amber-50/40 p-6 shadow-soft dark:border-brand-900/35 dark:from-brand-950/25 dark:via-slate-900 dark:to-slate-900/95">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <h3 className="font-display text-lg font-bold text-slate-900 dark:text-white">
            {t("app.insightPanelTitle", { insight: ENGINE_INSIGHT })}
          </h3>
          <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-400">{t("app.insightPanelSubtitle")}</p>
          <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-500">{t("app.insightBillingFootnote")}</p>
        </div>
        {gp + gc > 0 ? (
          <UsageDetailsToggle compact className="shrink-0">
            <div className="flex flex-col gap-2">
              <span className="inline-block rounded-full border border-brand-200/70 bg-white/70 px-2.5 py-1 text-[10px] text-slate-600 dark:border-brand-800/60 dark:bg-slate-950/60 dark:text-slate-400">
                {t("editor.insightTok", { insight: ENGINE_INSIGHT, n: gp + gc })}
              </span>
              {insightOptMru > 0 ? (
                <span className="inline-block rounded-full border border-amber-200/80 bg-amber-50/90 px-2.5 py-1 text-[10px] font-medium text-amber-950 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-100">
                  {t("app.insightBilledMruLine", { n: formatMru(insightOptMru) })}
                </span>
              ) : null}
            </div>
          </UsageDetailsToggle>
        ) : null}
      </div>

      <label className="mt-5 block">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-600 dark:text-slate-400">
          {t("app.subjectLabel")}
        </span>
        <input
          type="text"
          value={subject}
          onChange={(ev) => onSubjectChange(ev.target.value)}
          className="mt-2 w-full rounded-xl border border-slate-200/90 bg-white/90 px-4 py-2.5 text-sm text-slate-900 shadow-inner outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-400/35 dark:border-slate-700 dark:bg-slate-950/65 dark:text-slate-50"
          spellCheck={false}
          placeholder={t("common.general")}
        />
      </label>

      {truncated ? (
        <p className="mt-2 text-[11px] text-amber-800 dark:text-amber-300/90">{t("app.insightTruncatedNote")}</p>
      ) : null}

      {deepSummary?.trim() ? (
        <>
          <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">{t("app.deepSummaryHeading")}</span>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(deepSummary.trim());
                window.dispatchEvent(
                  new CustomEvent("lecturai-toast", { detail: { msg: t("app.deepSummaryCopied"), type: "success" } }),
                );
              }}
              className="rounded-lg border border-slate-200/90 bg-white/80 px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/75 dark:text-slate-200"
            >
              {t("app.copyDeepSummary")}
            </button>
          </div>
          <article className="mt-3 max-h-[52vh] overflow-y-auto rounded-2xl border border-slate-200/80 bg-white/70 px-4 py-4 text-start dark:border-slate-700/70 dark:bg-slate-950/40">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdMini}>
              {deepSummary.trim()}
            </ReactMarkdown>
          </article>
        </>
      ) : null}

      {typeof onClose === "function" ? (
        <div className="mt-6 flex justify-end border-t border-slate-200/80 pt-4 dark:border-slate-700/80">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-200/90 bg-white/90 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-white dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200"
          >
            {t("app.insightHideBtn")}
          </button>
        </div>
      ) : null}
    </section>
  );
}
