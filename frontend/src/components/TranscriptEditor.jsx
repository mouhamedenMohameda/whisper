import { useState } from "react";
import { ENGINE_TRANSCRIPTION } from "../branding.js";
import TranscriptMixedView from "./TranscriptMixedView.jsx";
import { estimateTokensFromText, formatMru } from "../utils/usage.js";

export default function TranscriptEditor({
  value,
  onChange,
  language,
  speechLanguageChosen,
  wordCount,
  durationMinutes,
  primaryFileName,
  onExportTxt,
  usage = {},
  mixedView = null,
}) {
  const [showTextFallback, setShowTextFallback] = useState(false);
  const liveTok = estimateTokensFromText(value);
  const secs = usage?.whisperAudioSeconds ?? 0;
  const audioMinDisp = secs > 0 ? (secs / 60).toFixed(2) : null;
  const apiTokSum = usage?.whisperApiEstimatedTokensSum ?? 0;
  const mixPrompt = Number(usage?.mixedLangPromptTokens ?? 0) || 0;
  const mixComp = Number(usage?.mixedLangCompletionTokens ?? 0) || 0;
  const foreignN = Number(mixedView?.foreign_segment_count ?? 0) || 0;
  const highRelN = Number(mixedView?.high_reliability_block_count ?? 0) || 0;
  /** Vue structurée (couleurs sémantiques) — source principale si alignée avec le texte cours. */
  const unifiedPrimary = Array.isArray(mixedView?.blocks) && mixedView.blocks.length > 0;
  const violetInSync =
    unifiedPrimary && typeof mixedView?.plain_text === "string" && mixedView.plain_text === value;
  const showVioletHero = violetInSync;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {speechLanguageChosen ? (
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-950 dark:border-emerald-800 dark:bg-emerald-950/35 dark:text-emerald-100">
            Langue choisie : <span className="capitalize">{speechLanguageChosen}</span>
          </span>
        ) : null}
        <span className="rounded-full border border-slate-200 px-3 py-1 text-xs dark:border-slate-700 dark:text-slate-300">
          🌐 Sortie Whisper&nbsp;: <span className="font-medium">{language || "—"}</span>
        </span>
        <span className="rounded-full border border-slate-200 px-3 py-1 text-xs dark:border-slate-700 dark:text-slate-300">
          📝 ~{wordCount || 0} mots
        </span>
        {durationMinutes > 0 && (
          <span className="rounded-full border border-slate-200 px-3 py-1 text-xs dark:border-slate-700 dark:text-slate-300">
            ⏱ ~{durationMinutes} min audio
          </span>
        )}
        <span className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs dark:border-violet-800 dark:bg-violet-950/50 dark:text-violet-100">
          🧮 ~{liveTok} jetons <span className="opacity-75">(texte · estim.)</span>
        </span>
        {(apiTokSum > 0 || secs > 0) && (
          <span className="rounded-full border border-slate-200 px-3 py-1 text-xs dark:border-slate-700 dark:text-slate-300">
            Whisper · ~{apiTokSum || "—"} jetons
            {audioMinDisp ? ` · ${audioMinDisp} min réel` : ""}
          </span>
        )}
        <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
          💰 Transcription&nbsp;: ~{formatMru(usage?.whisperBilledMru ?? 0)} MRU
        </span>
        {unifiedPrimary && foreignN > 0 && showVioletHero ? (
          <span className="rounded-full border border-red-200/90 bg-red-50 px-3 py-1 text-xs font-medium text-red-900 dark:border-red-900/60 dark:bg-red-950/45 dark:text-red-100">
            🌍 {foreignN > 1 ? `${foreignN} autres langues` : "1 autre langue"}
          </span>
        ) : unifiedPrimary && showVioletHero ? (
          <span className="rounded-full border border-violet-200 px-3 py-1 text-xs dark:border-violet-800 dark:text-violet-200/90">
            Transcription vue unifiée
          </span>
        ) : unifiedPrimary && !showVioletHero ? (
          <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
            Édition manuelle — surlignage désactivé
          </span>
        ) : null}
        {unifiedPrimary && showVioletHero && highRelN > 0 ? (
          <span className="rounded-full border border-violet-300/70 bg-violet-50 px-3 py-1 text-xs font-medium text-violet-950 dark:border-violet-700 dark:bg-violet-950/50 dark:text-violet-100">
            ✓ {highRelN > 1 ? `${highRelN} passages très nets` : "1 passage très net"}
          </span>
        ) : null}
        {mixPrompt + mixComp > 0 ? (
          <span className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 dark:border-slate-600 dark:text-slate-400">
            Langues (GPT)&nbsp;: ~{mixPrompt + mixComp} jetons
          </span>
        ) : null}
      </div>

      {unifiedPrimary && showVioletHero ? (
        <>
          <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-400">
            <span className="font-semibold text-red-600 dark:text-red-400">Rouge</span> : autre langue.{" "}
            <span className="font-semibold text-violet-700 dark:text-violet-300">Violet</span> : passage très net à
            l’oral. C’est ce texte qui sert pour générer le cours.
          </p>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-display text-base font-bold text-slate-900 dark:text-white">Transcript</h3>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  const txt = typeof value === "string" ? value : "";
                  navigator.clipboard.writeText(txt);
                  window.dispatchEvent(
                    new CustomEvent("lecturai-toast", {
                      detail: { msg: "Transcription copiée.", type: "success" },
                    }),
                  );
                }}
                className="rounded-xl border border-violet-200/90 bg-white/70 px-3 py-1.5 text-xs font-semibold text-violet-800 shadow-sm transition hover:bg-white dark:border-violet-900 dark:bg-violet-950/50 dark:text-violet-100 dark:hover:bg-violet-950"
              >
                📋 Copier
              </button>
              <button
                type="button"
                onClick={onExportTxt}
                className="rounded-xl border border-slate-200/90 bg-white/70 px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                📝 Exporter .txt
              </button>
            </div>
          </div>
          <TranscriptMixedView view={mixedView} emptyHint="Vue vide — réessaie la transcription." />
          <button
            type="button"
            onClick={() => setShowTextFallback((s) => !s)}
            className="text-xs font-semibold text-slate-600 underline underline-offset-2 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
          >
            {showTextFallback ? "▼ Masquer" : "▶"} Éditer le texte seul
          </button>
          {showTextFallback ? (
            <textarea
              value={value}
              onChange={(e) => onChange(e.target.value)}
              spellCheck
              aria-label="Texte brut pour affinage avant génération du cours"
              className="glass-panel min-h-[220px] w-full rounded-2xl border border-slate-200/90 p-4 text-sm leading-relaxed text-slate-900 outline-none ring-brand-500/15 focus:border-brand-500 focus:ring-4 dark:border-slate-700 dark:!bg-slate-950/75 dark:text-slate-100"
            />
          ) : null}
        </>
      ) : unifiedPrimary && !showVioletHero ? (
        <>
          <p className="text-sm leading-relaxed text-amber-900/85 dark:text-amber-100/90">
            Texte modifié à la main — les couleurs sont masquées. « Revenir au texte unifiée » restaure la version
            surlignée.
          </p>
          <textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            spellCheck
            className="glass-panel min-h-[320px] w-full rounded-2xl border border-slate-200/90 p-4 text-sm leading-relaxed text-slate-900 outline-none ring-brand-500/15 focus:border-brand-500 focus:ring-4 dark:border-slate-700 dark:!bg-slate-950/75 dark:text-slate-100"
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                if (typeof mixedView?.plain_text === "string") onChange(mixedView.plain_text);
                window.dispatchEvent(
                  new CustomEvent("lecturai-toast", {
                    detail: { msg: "Texte réinitialisé depuis la vue unifiée.", type: "success" },
                  }),
                );
              }}
              className="rounded-xl border border-violet-200 px-4 py-2 text-xs font-semibold text-violet-800 hover:bg-violet-50 dark:border-violet-800 dark:text-violet-100 dark:hover:bg-violet-950/50"
            >
              Revenir au texte unifiée (auto)
            </button>
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(value)}
              className="rounded-xl border border-slate-200/90 px-4 py-2 text-xs font-semibold text-slate-700 dark:border-slate-600 dark:text-slate-200"
            >
              📋 Copier
            </button>
            <button type="button" onClick={onExportTxt} className="rounded-xl border border-slate-200/90 px-4 py-2 text-xs font-semibold text-slate-700 dark:border-slate-600 dark:text-slate-200">
              📝 Exporter .txt
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-400">
            {ENGINE_TRANSCRIPTION} reste littéral : relis le bloc et corrige les noms, acronymes et découpures avant de
            lancer la génération du cours.
          </p>
          <textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            spellCheck
            className="glass-panel min-h-[320px] w-full rounded-2xl border border-slate-200/90 p-4 text-sm leading-relaxed text-slate-900 outline-none ring-brand-500/15 focus:border-brand-500 focus:ring-4 dark:border-slate-700 dark:!bg-slate-950/75 dark:text-slate-100"
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(value);
                window.dispatchEvent(
                  new CustomEvent("lecturai-toast", {
                    detail: { msg: "Transcript copié.", type: "success" },
                  }),
                );
              }}
              className="rounded-2xl border border-slate-200/90 bg-white/70 px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              📋 Copier
            </button>
            <button
              type="button"
              onClick={onExportTxt}
              className="rounded-2xl border border-slate-200/90 bg-white/70 px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              📝 Exporter .txt
            </button>
            <span className="self-center text-xs text-slate-400">
              {primaryFileName ? `Source: ${primaryFileName}` : ""}
            </span>
          </div>
        </>
      )}

      {unifiedPrimary ? (
        <p className="text-xs text-slate-400">
          {primaryFileName ? `Source: ${primaryFileName}` : ""}
        </p>
      ) : null}
    </div>
  );
}
