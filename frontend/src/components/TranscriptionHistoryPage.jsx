import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import { formatMru } from "../utils/usage.js";

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("fr-FR", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

/** @param {{ speechLanguage?: string; language?: string }} row */
function formatLanguageCell(row) {
  const picked = row.speechLanguage === "ar" ? "Arabe" : "Français";
  const wh = row.language || "—";
  return `${picked} · ${wh}`;
}

/** @param {{ usage?: Record<string, unknown> } & { whisperMin: string; mruEst: number }} p */
function TechnicalConsumptionBlock({ usage = {}, whisperMin, mruEst, t }) {
  const u = usage;
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-1 gap-3 text-xs text-slate-600 dark:text-slate-400 sm:grid-cols-3">
        <div>
          <dt className="text-slate-500 dark:text-slate-500">{t("history.colDur")}</dt>
          <dd className="font-medium text-slate-800 dark:text-slate-200">
            {whisperMin === "—" ? "—" : `${whisperMin} min`}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500 dark:text-slate-500">{t("history.colTok")}</dt>
          <dd className="tabular-nums font-medium text-slate-800 dark:text-slate-200">
            {Number(u.whisperApiEstimatedTokensSum || 0).toLocaleString("fr-FR")}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500 dark:text-slate-500">{t("history.colMru")}</dt>
          <dd className="font-semibold text-brand-700 dark:text-brand-300">~{formatMru(mruEst)} MRU</dd>
        </div>
      </dl>
      <div className="flex flex-wrap gap-2 pt-1 border-t border-slate-100 dark:border-slate-800">
         {u.transcription_engine && (
           <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-400">
             ⚙️ {u.transcription_engine} {u.retail_mru_per_hour_applied != null ? `· ${u.retail_mru_per_hour_applied} MRU/h` : ""}
           </span>
         )}
      </div>
    </div>
  );
}

export default function TranscriptionHistoryPage({ items, onBack, onOpen, onDelete }) {
  const { t } = useTranslation();
  const [expandedId, setExpandedId] = useState(/** @type {string | null} */ (null));

  const toggleExpanded = (id) => {
    setExpandedId((cur) => (cur === id ? null : id));
  };

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t("history.title")}</h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{t("history.subtitle")}</p>
        </div>
        <button
          type="button"
          onClick={onBack}
          className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-900"
        >
          {t("history.backHome")}
        </button>
      </div>

      {items.length === 0 ? (
        <div className="glass-panel rounded-3xl border border-dashed border-slate-300/80 p-12 text-center dark:border-slate-700 dark:!bg-slate-900/40">
          <p className="text-slate-600 dark:text-slate-400">{t("history.empty")}</p>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-500">{t("history.emptyHint")}</p>
        </div>
      ) : (
        <>
          <div className="glass-panel hidden overflow-hidden rounded-3xl shadow-soft dark:!bg-slate-900/70 lg:block">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-400">
                <tr>
                  <th className="px-4 py-3">{t("history.colDate")}</th>
                  <th className="px-4 py-3">{t("history.colFiles")}</th>
                  <th className="px-4 py-3">{t("history.colLang")}</th>
                  <th className="px-4 py-3">{t("history.colCourse")}</th>
                  <th className="px-4 py-3 text-right">{t("history.colActions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {items.map((row) => {
                  const u = row.usage || {};
                  const whisperMin = u.whisperAudioSeconds ? (u.whisperAudioSeconds / 60).toFixed(2) : "—";
                  const hasLesson = Boolean(row.lesson && row.lesson.length > 0);
                  const mruEst =
                    Number(u.whisperBilledMru || 0) +
                    Number(u.groqLessonBilledMru ?? u.claudeBilledMru ?? 0) +
                    Number(u.groqInsightOptionalBilledMru ?? 0);
                  const expanded = expandedId === row.id;
                  return (
                    <Fragment key={row.id}>
                      <tr className="hover:bg-slate-50/80 dark:hover:bg-slate-800/60">
                        <td className="whitespace-nowrap px-4 py-3 text-slate-700 dark:text-slate-300">
                          {formatDate(row.createdAt)}
                        </td>
                        <td className="max-w-[200px] px-4 py-3">
                          <div className="truncate font-medium text-slate-900 dark:text-white" title={row.displayTitle}>
                            {row.displayTitle}
                          </div>
                          {row.filenames?.length > 1 && (
                            <div className="mt-0.5 text-[11px] text-slate-500">
                              {t("history.moreFiles", { n: row.filenames.length - 1 })}
                            </div>
                          )}
                        </td>
                        <td className="max-w-[14rem] px-4 py-3 text-slate-600 dark:text-slate-400">
                          {formatLanguageCell(row)}
                        </td>
                        <td className="px-4 py-3">
                          {hasLesson ? (
                            <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">
                              {t("history.generated")}
                            </span>
                          ) : (
                            <span className="rounded-full bg-slate-500/15 px-2 py-0.5 text-[11px] text-slate-600 dark:text-slate-400">
                              —
                            </span>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-right">
                          <button
                            type="button"
                            onClick={() => toggleExpanded(row.id)}
                            className="rounded-lg px-2 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
                            title={expanded ? t("usageDetails.hide") : t("usageDetails.show")}
                          >
                            {expanded ? t("usageDetails.hide") : t("usageDetails.showShort")}
                          </button>
                          <button
                            type="button"
                            onClick={() => onOpen(row)}
                            className="ml-1 rounded-lg px-2 py-1 text-xs font-semibold text-brand-600 hover:bg-brand-50 dark:text-brand-400 dark:hover:bg-brand-950/50"
                          >
                            {t("history.open")}
                          </button>
                          <button
                            type="button"
                            onClick={() => onDelete(row.id)}
                            className="ml-1 rounded-lg px-2 py-1 text-xs font-semibold text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40"
                          >
                            {t("history.delete")}
                          </button>
                        </td>
                      </tr>
                      {expanded ? (
                        <tr className="bg-slate-50/90 dark:bg-slate-900/90">
                          <td colSpan={5} className="px-4 py-4">
                            <TechnicalConsumptionBlock usage={u} whisperMin={whisperMin} mruEst={mruEst} t={t} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          <ul className="space-y-3 lg:hidden">
            {items.map((row) => {
              const u = row.usage || {};
              const whisperMin = u.whisperAudioSeconds ? (u.whisperAudioSeconds / 60).toFixed(2) : "—";
              const hasLesson = Boolean(row.lesson && row.lesson.length > 0);
              const mruEst =
                Number(u.whisperBilledMru || 0) +
                Number(u.groqLessonBilledMru ?? u.claudeBilledMru ?? 0) +
                Number(u.groqInsightOptionalBilledMru ?? 0);
              const expanded = expandedId === row.id;
              return (
                <li
                  key={row.id}
                  className="glass-panel rounded-2xl border border-slate-200/80 p-4 shadow-sm dark:border-slate-800 dark:!bg-slate-900/60"
                >
                  <div className="font-medium text-slate-900 dark:text-white">{row.displayTitle}</div>
                  <div className="mt-1 text-xs text-slate-500">{formatDate(row.createdAt)}</div>
                  <dl className="mt-3 grid grid-cols-1 gap-2 text-xs text-slate-600 dark:text-slate-400">
                    <div>
                      <dt className="text-slate-500">{t("history.colLang")}</dt>
                      <dd>{formatLanguageCell(row)}</dd>
                    </div>
                  </dl>
                  {expanded ? (
                    <div className="mt-3 border-t border-slate-100 pt-3 dark:border-slate-800">
                      <TechnicalConsumptionBlock usage={u} whisperMin={whisperMin} mruEst={mruEst} t={t} />
                    </div>
                  ) : null}
                  <div className="mt-2">
                    {hasLesson ? (
                      <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">
                        {t("history.courseGen")}
                      </span>
                    ) : (
                      <span className="text-[11px] text-slate-500">{t("history.noCourse")}</span>
                    )}
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => toggleExpanded(row.id)}
                      className="rounded-xl border border-slate-200/90 px-3 py-2 text-xs font-semibold text-slate-700 dark:border-slate-600 dark:text-slate-200"
                      title={expanded ? t("usageDetails.hide") : t("usageDetails.show")}
                    >
                      {expanded ? t("usageDetails.hide") : t("usageDetails.showShort")}
                    </button>
                    <button
                      type="button"
                      onClick={() => onOpen(row)}
                      className="flex-1 rounded-xl bg-brand-600 py-2 text-sm font-semibold text-white dark:bg-brand-500"
                    >
                      {t("history.open")}
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(row.id)}
                      className="rounded-xl border border-rose-200 px-4 py-2 text-sm font-semibold text-rose-600 dark:border-rose-900 dark:text-rose-400"
                    >
                      {t("history.delete")}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
