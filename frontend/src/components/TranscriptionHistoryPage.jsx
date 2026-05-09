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

export default function TranscriptionHistoryPage({ items, onBack, onOpen, onDelete }) {
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Historique des transcriptions</h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Sauvegardé sur cet appareil (navigateur). Les lignes précédentes restent consultables après rechargement
            de la page.
          </p>
        </div>
        <button
          type="button"
          onClick={onBack}
          className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-900"
        >
          ← Retour à l&apos;accueil
        </button>
      </div>

      {items.length === 0 ? (
        <div className="glass-panel rounded-3xl border border-dashed border-slate-300/80 p-12 text-center dark:border-slate-700 dark:!bg-slate-900/40">
          <p className="text-slate-600 dark:text-slate-400">Aucune transcription enregistrée pour le moment.</p>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-500">
            Lance une transcription depuis l&apos;accueil pour remplir l&apos;historique.
          </p>
        </div>
      ) : (
        <>
          <div className="glass-panel hidden overflow-hidden rounded-3xl shadow-soft dark:!bg-slate-900/70 lg:block">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-400">
                <tr>
                  <th className="px-4 py-3">Date</th>
                  <th className="px-4 py-3">Fichier(s)</th>
                  <th className="px-4 py-3">Langue</th>
                  <th className="px-4 py-3">Durée audio</th>
                  <th className="px-4 py-3 text-right">Jetons (transcr.)</th>
                  <th className="px-4 py-3 text-right">MRU</th>
                  <th className="px-4 py-3">Cours</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {items.map((row) => {
                  const u = row.usage || {};
                  const whisperMin = u.whisperAudioSeconds
                    ? (u.whisperAudioSeconds / 60).toFixed(2)
                    : "—";
                  const hasLesson = Boolean(row.lesson && row.lesson.length > 0);
                  const mruEst = Number(u.whisperBilledMru || 0) + Number(u.claudeBilledMru || 0);
                  return (
                    <tr key={row.id} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/60">
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700 dark:text-slate-300">
                        {formatDate(row.createdAt)}
                      </td>
                      <td className="max-w-[200px] px-4 py-3">
                        <div className="truncate font-medium text-slate-900 dark:text-white" title={row.displayTitle}>
                          {row.displayTitle}
                        </div>
                        {row.filenames?.length > 1 && (
                          <div className="mt-0.5 text-[11px] text-slate-500">
                            +{row.filenames.length - 1} autre(s) fichier(s)
                          </div>
                        )}
                      </td>
                      <td className="max-w-[14rem] px-4 py-3 text-slate-600 dark:text-slate-400">
                        {formatLanguageCell(row)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                        {whisperMin === "—" ? "—" : `${whisperMin} min`}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-700 dark:text-slate-300">
                        {Number(u.whisperApiEstimatedTokensSum || 0).toLocaleString("fr-FR")}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right font-medium text-brand-700 dark:text-brand-300">
                        ~{formatMru(mruEst)} MRU
                      </td>
                      <td className="px-4 py-3">
                        {hasLesson ? (
                          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">
                            Généré
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
                          onClick={() => onOpen(row)}
                          className="rounded-lg px-2 py-1 text-xs font-semibold text-brand-600 hover:bg-brand-50 dark:text-brand-400 dark:hover:bg-brand-950/50"
                        >
                          Ouvrir
                        </button>
                        <button
                          type="button"
                          onClick={() => onDelete(row.id)}
                          className="ml-2 rounded-lg px-2 py-1 text-xs font-semibold text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40"
                        >
                          Supprimer
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <ul className="space-y-3 lg:hidden">
            {items.map((row) => {
              const u = row.usage || {};
              const whisperMin = u.whisperAudioSeconds
                ? (u.whisperAudioSeconds / 60).toFixed(2)
                : "—";
              const hasLesson = Boolean(row.lesson && row.lesson.length > 0);
              const mruEst = Number(u.whisperBilledMru || 0) + Number(u.claudeBilledMru || 0);
              return (
                <li
                  key={row.id}
                  className="glass-panel rounded-2xl border border-slate-200/80 p-4 shadow-sm dark:border-slate-800 dark:!bg-slate-900/60"
                >
                  <div className="font-medium text-slate-900 dark:text-white">{row.displayTitle}</div>
                  <div className="mt-1 text-xs text-slate-500">{formatDate(row.createdAt)}</div>
                  <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs text-slate-600 dark:text-slate-400">
                    <div>
                      <dt className="text-slate-500">Langue</dt>
                      <dd>{formatLanguageCell(row)}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Audio</dt>
                      <dd>{whisperMin === "—" ? "—" : `${whisperMin} min`}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Jetons (transcr.)</dt>
                      <dd>{Number(u.whisperApiEstimatedTokensSum || 0).toLocaleString("fr-FR")}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">MRU (~)</dt>
                      <dd className="font-semibold text-brand-700 dark:text-brand-300">
                        ~{formatMru(mruEst)} MRU
                      </dd>
                    </div>
                  </dl>
                  <div className="mt-2">
                    {hasLesson ? (
                      <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">
                        Cours généré
                      </span>
                    ) : (
                      <span className="text-[11px] text-slate-500">Pas encore de cours</span>
                    )}
                  </div>
                  <div className="mt-4 flex gap-2">
                    <button
                      type="button"
                      onClick={() => onOpen(row)}
                      className="flex-1 rounded-xl bg-brand-600 py-2 text-sm font-semibold text-white dark:bg-brand-500"
                    >
                      Ouvrir
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(row.id)}
                      className="rounded-xl border border-rose-200 px-4 py-2 text-sm font-semibold text-rose-600 dark:border-rose-900 dark:text-rose-400"
                    >
                      Supprimer
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
