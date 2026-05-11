import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ENGINE_TRANSCRIPTION } from "../branding.js";
import { cancelTranscriptionJob, listTranscriptionJobs } from "../utils/api.js";
import { forgetBgTranscribeJobId, loadBgTranscribeJobIds } from "../utils/bgTranscribeJobsStorage.js";
import { userFacingTranscriptionJobFailure } from "../utils/transcribeUserMessages.js";

function formatDateIso(iso) {
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

function formatDurMin(sec, under1m) {
  if (typeof sec !== "number" || !Number.isFinite(sec) || sec <= 0) return "—";
  const m = Math.round(sec / 60);
  if (m <= 0) return under1m;
  return `${m}m`;
}

/**
 * Liste serveur alignée avec un tableau type « fichier récent » ; le pourcentage vient du serveur (reprise après rechargement ou autre appareil lorsque même compte).
 *
 * @param {{ authReady: boolean; onOpenJob?: (row: Record<string, unknown>) => void; onWalletUpdated?: () => void }} props
 */
export default function BgTranscribeJobsPanel({ authReady, onOpenJob, onWalletUpdated }) {
  const { t } = useTranslation();
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");

  const trackedLocalIds = useMemo(() => loadBgTranscribeJobIds(), [rows]);

  async function reloadList() {
    try {
      setErr("");
      const items = await listTranscriptionJobs();
      setRows(items);
    } catch (e) {
      setErr(e?.message ? String(e.message) : String(e || ""));
    }
  }

  useEffect(() => {
    if (!authReady) return undefined;
    let cancelled = false;

    async function load() {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        setErr("");
        const items = await listTranscriptionJobs();
        if (!cancelled) setRows(items);
      } catch (e) {
        if (!cancelled) setErr(e?.message ? String(e.message) : String(e || ""));
      }
    }

    void load();
    const pollTimer = setInterval(() => void load(), 8000);
    const onVisibility = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelled = true;
      clearInterval(pollTimer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [authReady]);

  if (!authReady || (rows.length === 0 && !err && trackedLocalIds.length === 0)) return null;

  return (
    <section className="glass-panel mx-auto mt-10 w-full max-w-6xl rounded-3xl shadow-soft dark:!bg-slate-900/70">
      <div className="flex flex-wrap items-center gap-4 border-b border-slate-200/80 px-5 py-4 dark:border-slate-800">
        <div className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-white">
          <span aria-hidden className="text-brand-600 dark:text-brand-400">
            ▦
          </span>
          {t("bgJobs.title", { engine: ENGINE_TRANSCRIPTION })}
        </div>
        {trackedLocalIds.length > 0 && (
          <span className="rounded-full bg-brand-600/15 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-brand-800 dark:bg-brand-500/20 dark:text-brand-300">
            {t("bgJobs.localTrack", { count: trackedLocalIds.length })}
          </span>
        )}
      </div>
      {!err && typeof onOpenJob === "function" ? (
        <p className="border-b border-slate-200/70 px-5 py-2 text-[11px] leading-relaxed text-slate-600 dark:border-slate-800 dark:text-slate-400">
          {t("bgJobs.serverPersistHint")} {t("bgJobs.clickRowsHint")}
        </p>
      ) : null}
      {err ? (
        <p className="px-5 py-4 text-xs text-rose-600 dark:text-rose-400">{err}</p>
      ) : (
        <div className="overflow-x-auto px-3 pb-3">
          <table className="w-full text-left text-sm">
            <thead className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              <tr>
                <th className="px-3 py-2">{t("bgJobs.colName")}</th>
                <th className="px-3 py-2">{t("bgJobs.colAdded")}</th>
                <th className="px-3 py-2">{t("bgJobs.colDur")}</th>
                <th className="px-3 py-2">{t("bgJobs.colModel")}</th>
                <th className="px-3 py-2">{t("bgJobs.colStatus")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map((r) => {
                const st = String(r.status || "");
                const name = String(r.original_filename || "").trim() || "Audio";
                const pct = typeof r.progress_percent === "number" ? r.progress_percent : 0;
                const isTrackedHere = trackedLocalIds.includes(String(r.job_id));
                const canOpen = typeof onOpenJob === "function";
                const rowKey = String(r.job_id);
                const activate = () => {
                  if (!canOpen) return;
                  /** @type {Record<string, unknown>} */
                  const row = typeof r === "object" && r !== null ? /** @type {Record<string, unknown>} */ (r) : {};
                  onOpenJob(row);
                };
                return (
                  <tr
                    key={rowKey}
                    className={`text-slate-700 dark:text-slate-300 ${
                      canOpen
                        ? "cursor-pointer hover:bg-slate-50/95 focus-within:bg-slate-50/95 dark:hover:bg-slate-800/40 dark:focus-within:bg-slate-800/40"
                        : ""
                    }`}
                    role={canOpen ? "button" : undefined}
                    tabIndex={canOpen ? 0 : undefined}
                    aria-label={canOpen ? t("bgJobs.rowAria", { name }) : undefined}
                    onClick={canOpen ? () => void activate() : undefined}
                    onKeyDown={
                      canOpen
                        ? (ev) => {
                            if (ev.key === "Enter" || ev.key === " ") {
                              ev.preventDefault();
                              void activate();
                            }
                          }
                        : undefined
                    }
                  >
                    <td className="max-w-[14rem] truncate px-3 py-2 font-medium text-slate-900 dark:text-white">
                      {name}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs">{formatDateIso(r.updated_at)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs">
                      {formatDurMin(Number(r.estimated_duration_seconds ?? 0), t("bgJobs.under1m"))}
                    </td>
                    <td
                      className="px-3 py-2 text-center text-xl"
                      aria-label={ENGINE_TRANSCRIPTION}
                      title={ENGINE_TRANSCRIPTION}
                    >
                      🎙️
                    </td>
                    <td className="px-3 py-2 font-semibold">
                      {st === "done" ? (
                        <span className="text-emerald-600 dark:text-emerald-400" title={t("bgJobs.doneTitle")}>
                          ✅
                        </span>
                      ) : st === "failed" ? (
                        <span className="max-w-[14rem] text-xs text-rose-600 dark:text-rose-400">
                          {userFacingTranscriptionJobFailure(r.error_detail, r.message, t)}
                        </span>
                      ) : st === "cancelled" ? (
                        <span className="text-slate-500 dark:text-slate-400" title={t("bgJobs.cancelledTitle")}>
                          {t("bgJobs.cancelledBadge")}
                        </span>
                      ) : st === "queued" ? (
                        <span className="flex flex-wrap items-center gap-2">
                          <span
                            className="tabular-nums text-slate-600 dark:text-slate-300"
                            title={t("bgJobs.queuedTitle")}
                          >
                            {t("bgJobs.queuedBadge")}
                          </span>
                          <button
                            type="button"
                            className="rounded-lg border border-slate-200/90 bg-white/90 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-700 shadow-sm hover:bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                            onClick={(ev) => {
                              ev.stopPropagation();
                              void (async () => {
                                try {
                                  await cancelTranscriptionJob(rowKey);
                                  forgetBgTranscribeJobId(rowKey);
                                  await reloadList();
                                  if (typeof onWalletUpdated === "function") onWalletUpdated();
                                  window.dispatchEvent(
                                    new CustomEvent("lecturai-toast", {
                                      detail: { msg: t("bgJobs.cancelOk"), type: "success" },
                                    }),
                                  );
                                } catch (ce) {
                                  window.dispatchEvent(
                                    new CustomEvent("lecturai-toast", {
                                      detail: {
                                        msg: ce?.message ? String(ce.message) : t("bgJobs.cancelFail"),
                                        type: "error",
                                      },
                                    }),
                                  );
                                }
                              })();
                            }}
                          >
                            {t("bgJobs.cancelJob")}
                          </button>
                        </span>
                      ) : (
                        <span
                          className="tabular-nums text-brand-600 dark:text-brand-400"
                          title={
                            isTrackedHere ? t("bgJobs.trackCloseTitle") : t("bgJobs.trackServerTitle")
                          }
                        >
                          {t("bgJobs.processing", { pct })}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {trackedLocalIds.some((jid) => !rows.some((r) => String(r.job_id) === jid)) ? (
            <p className="px-5 pb-3 text-[11px] text-slate-500 dark:text-slate-400">
              {t("bgJobs.orphanHint")}
              <button
                type="button"
                className="ms-1 font-semibold text-brand-600 underline-offset-4 hover:underline dark:text-brand-400"
                onClick={() => {
                  for (const j of trackedLocalIds) {
                    if (!rows.some((r) => String(r.job_id) === j)) forgetBgTranscribeJobId(j);
                  }
                }}
              >
                {t("bgJobs.orphanBtn")}
              </button>
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}
