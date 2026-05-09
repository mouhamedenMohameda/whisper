import { useEffect, useMemo, useState } from "react";
import { ENGINE_TRANSCRIPTION } from "../branding.js";
import { listTranscriptionJobs } from "../utils/api.js";
import { forgetBgTranscribeJobId, loadBgTranscribeJobIds } from "../utils/bgTranscribeJobsStorage.js";

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

function formatDurMin(sec) {
  if (typeof sec !== "number" || !Number.isFinite(sec) || sec <= 0) return "—";
  const m = Math.round(sec / 60);
  if (m <= 0) return "<1m";
  return `${m}m`;
}

/**
 * Liste serveur alignée avec un tableau type « fichier récent » ; le pourcentage vient du serveur (reprise après rechargement ou autre appareil lorsque même compte).
 */
export default function BgTranscribeJobsPanel({ authReady }) {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");

  const trackedLocalIds = useMemo(() => loadBgTranscribeJobIds(), [rows]);

  useEffect(() => {
    if (!authReady) return undefined;
    let cancelled = false;

    async function load() {
      try {
        setErr("");
        const items = await listTranscriptionJobs();
        if (!cancelled) setRows(items);
      } catch (e) {
        if (!cancelled) setErr(e?.message ? String(e.message) : String(e || ""));
      }
    }

    void load();
    const t = setInterval(() => void load(), 5300);
    return () => {
      cancelled = true;
      clearInterval(t);
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
          Fichiers récents · {ENGINE_TRANSCRIPTION}
        </div>
        {trackedLocalIds.length > 0 && (
          <span className="rounded-full bg-brand-600/15 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-brand-800 dark:bg-brand-500/20 dark:text-brand-300">
            {trackedLocalIds.length} suivi{trackedLocalIds.length > 1 ? "s" : ""} local(aux) — poursuite si tu fermes l’app
          </span>
        )}
      </div>
      {err ? (
        <p className="px-5 py-4 text-xs text-rose-600 dark:text-rose-400">{err}</p>
      ) : (
        <div className="overflow-x-auto px-3 pb-3">
          <table className="w-full text-left text-sm">
            <thead className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              <tr>
                <th className="px-3 py-2">Nom</th>
                <th className="px-3 py-2">Ajouté</th>
                <th className="px-3 py-2">Durée (est.)</th>
                <th className="px-3 py-2">Modèle</th>
                <th className="px-3 py-2">Statut</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map((r) => {
                const st = String(r.status || "");
                const name = String(r.original_filename || "").trim() || "Audio";
                const pct = typeof r.progress_percent === "number" ? r.progress_percent : 0;
                const isTrackedHere = trackedLocalIds.includes(String(r.job_id));
                return (
                  <tr key={String(r.job_id)} className="text-slate-700 dark:text-slate-300">
                    <td className="max-w-[14rem] truncate px-3 py-2 font-medium text-slate-900 dark:text-white">
                      {name}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs">{formatDateIso(r.updated_at)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs">
                      {formatDurMin(Number(r.estimated_duration_seconds ?? 0))}
                    </td>
                    <td className="px-3 py-2 text-xl" aria-label="OpenAI Whisper" title={ENGINE_TRANSCRIPTION}>
                      🐳
                    </td>
                    <td className="px-3 py-2 font-semibold">
                      {st === "done" ? (
                        <span className="text-emerald-600 dark:text-emerald-400" title="Terminé sur le serveur">
                          ✅
                        </span>
                      ) : st === "failed" ? (
                        <span className="text-rose-600 dark:text-rose-400 text-xs">{r.message || "Échec"}</span>
                      ) : (
                        <span
                          className="tabular-nums text-brand-600 dark:text-brand-400"
                          title={
                            isTrackedHere
                              ? "Job suivi même après fermeture (import terminé)."
                              : "Progression mise à jour côté serveur."
                          }
                        >
                          {pct}%
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
              Certains suivis sont encore hors liste (nouveau ou autre liste). Une fois terminés ils disparaîtront du stockage local.{" "}
              <button
                type="button"
                className="font-semibold text-brand-600 underline-offset-4 hover:underline dark:text-brand-400"
                onClick={() => {
                  for (const j of trackedLocalIds) {
                    if (!rows.some((r) => String(r.job_id) === j)) forgetBgTranscribeJobId(j);
                  }
                }}
              >
                Retirer les suivis hors liste
              </button>
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}
