import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ENGINE_BRIDGE,
  ENGINE_COURSE,
  ENGINE_TRANSCRIPTION,
} from "../branding.js";
import { formatMru } from "../utils/usage.js";
import UsageDetailsToggle from "./UsageDetailsToggle.jsx";

export default function TranscriptEditor({
  value,
  onChange,
  language,
  speechLanguageChosen,
  wordCount = 0,
  durationMinutes = 0,
  primaryFileName,
  onExportTxt,
  usage = {},
  mixedView = null,
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);

  const apiTokSum = Number(usage?.whisperApiEstimatedTokensSum || 0);
  const mixPrompt = Number(usage?.mixedLangPromptTokens || 0);
  const mixComp = Number(usage?.mixedLangCompletionTokens || 0);
  const liveTok = Math.round(wordCount * 1.35);
  const secs = usage?.whisperAudioSeconds || 0;
  const audioMinDisp = secs ? (secs / 60).toFixed(2) : null;

  /** Vue structurée (couleurs sémantiques) — source principale si alignée avec le texte cours. */
  const unifiedPrimary = Array.isArray(mixedView?.blocks) && mixedView.blocks.length > 0;
  const violetInSync =
    unifiedPrimary && typeof mixedView?.plain_text === "string" && mixedView.plain_text === value;
  const showVioletHero = violetInSync;

  // Mapping des labels moteurs
  const engineLabels = {
    "whisper-large-v3-turbo": "Réponse express",
    "whisper-large-v3": "Profil équilibré",
    "gpt-4o-mini-transcribe": "Transcription affinée",
    "whisper-1": "Excellence audio",
    "local": "Atelier privé (économique)",
  };
  const currentEngineLabel = engineLabels[usage?.transcriptionEngine] || usage?.transcriptionEngine || (usage?.transcriptionEngine ? "Moteur inconnu" : null);
  const currentRate = usage?.retailMruPerHourApplied;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {speechLanguageChosen && (
          <span className="rounded-full bg-emerald-500/10 px-4 py-2 text-sm font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">
            {t("editor.langChosen")}{speechLanguageChosen}
          </span>
        )}
        {language && (
          <span className="rounded-full border border-slate-200 bg-white/50 px-4 py-2 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-300">
            🌐 {t("editor.whisperOut")}{language}
          </span>
        )}
        <span className="rounded-full border border-slate-200 bg-white/50 px-4 py-2 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-300">
          📝 {t("editor.words", { n: wordCount })}
        </span>

        {mixedView?.foreign_segment_count > 0 ? (
          <span className="rounded-full border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
            🌎 {t("editor.langsOther", { count: mixedView.foreign_segment_count })}
          </span>
        ) : null}

        {mixedView?.high_reliability_block_count > 0 ? (
          <span className="rounded-full border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
            ✓ {t("editor.clearPassage", { count: mixedView.high_reliability_block_count })}
          </span>
        ) : null}
      </div>

      <UsageDetailsToggle compact className="mt-1">
        <div className="flex flex-wrap gap-2">
          {durationMinutes > 0 && (
            <span className="rounded-full border border-slate-200 px-3 py-1 text-xs dark:border-slate-700 dark:text-slate-300">
              ⏱ {t("editor.minAudio", { n: durationMinutes })}
            </span>
          )}
          <span className="rounded-full border border-brand-200 bg-brand-50/80 px-3 py-1 text-xs dark:border-brand-800 dark:bg-brand-950/50 dark:text-brand-100">
            🧮 {t("editor.tokEst", { n: liveTok })}
            <span className="opacity-75">{t("editor.tokEstHint")}</span>
          </span>
          {(apiTokSum > 0 || secs > 0) && (
            <span className="rounded-full border border-slate-200 px-3 py-1 text-xs dark:border-slate-700 dark:text-slate-300">
              {t("editor.whisperTok", {
                engine: ENGINE_TRANSCRIPTION,
                n: apiTokSum > 0 ? apiTokSum : t("common.dash"),
                min: audioMinDisp ? t("editor.realMin", { n: audioMinDisp }) : "",
              })}
            </span>
          )}
          <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
            💰 {t("editor.mruLine", { n: formatMru(usage?.whisperBilledMru ?? 0) })}
          </span>
          {mixPrompt + mixComp > 0 ? (
            <span className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 dark:border-slate-600 dark:text-slate-400">
              {t("editor.bridgeTok", { engine: ENGINE_BRIDGE, n: mixPrompt + mixComp })}
            </span>
          ) : null}
          {currentEngineLabel && (
            <span className="rounded-full border border-slate-200 bg-white/50 px-3 py-1 text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-400">
              ⚙️ {currentEngineLabel} {currentRate != null ? `· ${currentRate} MRU/h` : ""}
            </span>
          )}
        </div>
      </UsageDetailsToggle>

      <div className="group relative">
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setEditing(true)}
          onBlur={() => setEditing(false)}
          spellCheck={false}
          className={`min-h-[24rem] w-full resize-y rounded-3xl border border-slate-200 bg-white p-6 text-sm leading-relaxed text-slate-800 outline-none transition shadow-soft focus:border-brand-500 focus:ring-4 focus:ring-brand-500/10 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200 dark:focus:border-brand-400 dark:focus:ring-brand-400/10 sm:min-h-[30rem] sm:p-8 sm:text-base ${
            editing ? "ring-4 ring-brand-500/10 dark:ring-brand-400/10" : ""
          }`}
          placeholder={t("editor.placeholder")}
        />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 rounded-b-3xl bg-gradient-to-t from-white/80 to-transparent dark:from-slate-900/80" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-4 pt-2">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onExportTxt()}
            className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-750 dark:hover:text-white"
          >
            📥 {t("editor.exportTxt")}
          </button>
        </div>

        <p className="text-[11px] text-slate-500 dark:text-slate-500">
          {t("editor.autoSaveHint")}
        </p>
      </div>

      {showVioletHero ? (
        <div className="relative mt-8 animate-in fade-in slide-in-from-bottom-4 duration-700">
          <div className="absolute -inset-1 rounded-3xl bg-gradient-to-r from-brand-600 via-amber-500 to-rose-500 opacity-20 blur-xl dark:opacity-10" />
          <div className="relative rounded-3xl border border-brand-200/50 bg-gradient-to-br from-brand-50/50 via-white to-amber-50/30 p-6 shadow-sm dark:border-brand-900/30 dark:from-slate-900 dark:to-slate-950">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-4">
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-600 text-2xl shadow-glow">
                  ✨
                </div>
                <div>
                  <h3 className="font-display text-base font-bold text-slate-900 dark:text-white">
                    {t("editor.heroTitle", { course: ENGINE_COURSE })}
                  </h3>
                  <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                    {t("editor.heroSubtitle")}
                  </p>
                </div>
              </div>
              <div className="flex gap-2 self-end sm:self-auto">
                <button
                  type="button"
                  className="rounded-xl bg-brand-600 px-5 py-2.5 text-sm font-bold text-white shadow-soft transition hover:brightness-105 active:translate-y-0.5 dark:bg-brand-500"
                >
                  {t("editor.heroBtn")}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
