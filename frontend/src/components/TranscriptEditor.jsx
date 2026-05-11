import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { ENGINE_BRIDGE, ENGINE_TRANSCRIPTION } from "../branding.js";
import TranscriptMixedView from "./TranscriptMixedView.jsx";
import UsageDetailsToggle from "./UsageDetailsToggle.jsx";
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
  const { t } = useTranslation();
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
            {t("editor.langChosen")}
            <span className="inline">{speechLanguageChosen}</span>
          </span>
        ) : null}
        <span className="rounded-full border border-slate-200 px-3 py-1 text-xs dark:border-slate-700 dark:text-slate-300">
          🌐 {t("editor.whisperOut")}
          <span className="font-medium">{language || t("common.dash")}</span>
        </span>
        <span className="rounded-full border border-slate-200 px-3 py-1 text-xs dark:border-slate-700 dark:text-slate-300">
          📝 {t("editor.words", { n: wordCount || 0 })}
        </span>
        {unifiedPrimary && foreignN > 0 && showVioletHero ? (
          <span className="rounded-full border border-red-200/90 bg-red-50 px-3 py-1 text-xs font-medium text-red-900 dark:border-red-900/60 dark:bg-red-950/45 dark:text-red-100">
            🌍 {t("editor.langsOther", { count: foreignN })}
          </span>
        ) : unifiedPrimary && showVioletHero ? (
          <span className="rounded-full border border-violet-200 px-3 py-1 text-xs dark:border-violet-800 dark:text-violet-200/90">
            {t("editor.unifiedView")}
          </span>
        ) : unifiedPrimary && !showVioletHero ? (
          <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
            {t("editor.manualEdit")}
          </span>
        ) : null}
        {unifiedPrimary && showVioletHero && highRelN > 0 ? (
          <span className="rounded-full border border-violet-300/70 bg-violet-50 px-3 py-1 text-xs font-medium text-violet-950 dark:border-violet-700 dark:bg-violet-950/50 dark:text-violet-100">
            ✓ {t("editor.clearPassage", { count: highRelN })}
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
          <span className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs dark:border-violet-800 dark:bg-violet-950/50 dark:text-violet-100">
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
        </div>
      </UsageDetailsToggle>

      {unifiedPrimary && showVioletHero ? (
        <>
          <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-400">
            <Trans
              i18nKey="editor.legendUnified"
              components={{
                red: <span className="font-semibold text-red-600 dark:text-red-400" />,
                violet: <span className="font-semibold text-violet-700 dark:text-violet-300" />,
              }}
            />
          </p>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-display text-base font-bold text-slate-900 dark:text-white">{t("editor.transcriptHeading")}</h3>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  const txt = typeof value === "string" ? value : "";
                  navigator.clipboard.writeText(txt);
                  window.dispatchEvent(
                    new CustomEvent("lecturai-toast", {
                      detail: { msg: t("editor.copyOk"), type: "success" },
                    }),
                  );
                }}
                className="rounded-xl border border-violet-200/90 bg-white/70 px-3 py-1.5 text-xs font-semibold text-violet-800 shadow-sm transition hover:bg-white dark:border-violet-900 dark:bg-violet-950/50 dark:text-violet-100 dark:hover:bg-violet-950"
              >
                {t("editor.copy")}
              </button>
              <button
                type="button"
                onClick={onExportTxt}
                className="rounded-xl border border-slate-200/90 bg-white/70 px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                {t("editor.exportTxt")}
              </button>
            </div>
          </div>
          <TranscriptMixedView view={mixedView} emptyHint={t("editor.emptyUnified")} />
          <button
            type="button"
            onClick={() => setShowTextFallback((s) => !s)}
            className="text-xs font-semibold text-slate-600 underline underline-offset-2 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
          >
            {showTextFallback ? t("editor.toggleHide") : t("editor.toggleShow")}
            {t("editor.editPlainOnly")}
          </button>
          {showTextFallback ? (
            <textarea
              value={value}
              onChange={(e) => onChange(e.target.value)}
              spellCheck
              aria-label={t("editor.textareaUnifiedAria")}
              className="glass-panel min-h-[220px] w-full rounded-2xl border border-slate-200/90 p-4 text-sm leading-relaxed text-slate-900 outline-none ring-brand-500/15 focus:border-brand-500 focus:ring-4 dark:border-slate-700 dark:!bg-slate-950/75 dark:text-slate-100"
            />
          ) : null}
        </>
      ) : unifiedPrimary && !showVioletHero ? (
        <>
          <p className="text-sm leading-relaxed text-amber-900/85 dark:text-amber-100/90">{t("editor.manualWarn")}</p>
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
                    detail: { msg: t("editor.resetUnifiedToast"), type: "success" },
                  }),
                );
              }}
              className="rounded-xl border border-violet-200 px-4 py-2 text-xs font-semibold text-violet-800 hover:bg-violet-50 dark:border-violet-800 dark:text-violet-100 dark:hover:bg-violet-950/50"
            >
              {t("editor.restoreUnified")}
            </button>
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(value)}
              className="rounded-xl border border-slate-200/90 px-4 py-2 text-xs font-semibold text-slate-700 dark:border-slate-600 dark:text-slate-200"
            >
              {t("editor.copy")}
            </button>
            <button type="button" onClick={onExportTxt} className="rounded-xl border border-slate-200/90 px-4 py-2 text-xs font-semibold text-slate-700 dark:border-slate-600 dark:text-slate-200">
              {t("editor.exportTxt")}
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-400">{t("editor.literalHint", { engine: ENGINE_TRANSCRIPTION })}</p>
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
                    detail: { msg: t("editor.copyTranscriptOk"), type: "success" },
                  }),
                );
              }}
              className="rounded-2xl border border-slate-200/90 bg-white/70 px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              {t("editor.copy")}
            </button>
            <button
              type="button"
              onClick={onExportTxt}
              className="rounded-2xl border border-slate-200/90 bg-white/70 px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              {t("editor.exportTxt")}
            </button>
            <span className="self-center text-xs text-slate-400">{primaryFileName ? t("editor.source", { name: primaryFileName }) : ""}</span>
          </div>
        </>
      )}

      {unifiedPrimary ? <p className="text-xs text-slate-400">{primaryFileName ? t("editor.source", { name: primaryFileName }) : ""}</p> : null}
    </div>
  );
}
