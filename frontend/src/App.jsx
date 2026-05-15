import { lazy, Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import AuthScreen from "./components/AuthScreen.jsx";
import BgTranscribeJobsPanel from "./components/BgTranscribeJobsPanel.jsx";
import LanguageSwitcher from "./components/LanguageSwitcher.jsx";
import TranscriptEditor from "./components/TranscriptEditor.jsx";
import UploadZone from "./components/UploadZone.jsx";
import RouteErrorBoundary from "./components/RouteErrorBoundary.jsx";
import PublicLessonView, { publicShareTokenFromLocation } from "./components/PublicLessonView.jsx";
import WhatsAppSupportButton from "./components/WhatsAppSupportButton.jsx";
import TranscriptionRatingModal from "./components/TranscriptionRatingModal.jsx";
import IdeaFeedbackModal from "./components/IdeaFeedbackModal.jsx";
import InsightPanel from "./components/InsightPanel.jsx";
import { ENGINE_COURSE, ENGINE_INSIGHT, ENGINE_TRANSCRIPTION } from "./branding.js";
import {
  TRANSCRIBE_SERVER_WEIGHT,
  TRANSCRIBE_UPLOAD_WEIGHT,
  TranscriptionJobFailedError,
  apiUrl,
  enqueueTranscribeJobWithXHR,
  getTranscriptionJob,
  isUploadInterruptedError,
  generateLesson,
  requestTranscriptInsight,
  getAuthHeaders,
  parseJsonResponse,
  waitForTerminalTranscriptionJob,
} from "./utils/api.js";
import { loadBgTranscribeJobIds, forgetBgTranscribeJobId, persistBgTranscribeJobId } from "./utils/bgTranscribeJobsStorage.js";
import { clearAuthSession, getAuthProfile, getAuthToken, setAuthSession } from "./utils/authStorage.js";
import { captureReferralFromUrl } from "./utils/referral.js";
import { getEntry, loadHistory, prependEntry, removeEntry, updateEntry } from "./utils/transcriptionHistory.js";
import { mergeTranscriptMixedViews } from "./utils/transcriptMixedView.js";
import { userFacingTranscriptionJobFailure } from "./utils/transcribeUserMessages.js";
import i18n from "./i18n/index.js";
import { ratingPromptSignature, wasRatingPromptHandled } from "./utils/ratingPromptSession.js";

const AdminTopUpsPage = lazy(() => import("./components/AdminTopUpsPage.jsx"));
const ChatPage = lazy(() => import("./components/ChatPage.jsx"));
const CreditsPage = lazy(() => import("./components/CreditsPage.jsx"));
const LessonViewer = lazy(() => import("./components/LessonViewer.jsx"));
const NotificationsPage = lazy(() => import("./components/NotificationsPage.jsx"));
const TranscriptionHistoryPage = lazy(() => import("./components/TranscriptionHistoryPage.jsx"));

function RouteLazyFallback() {
  return (
    <div className="flex min-h-[32vh] w-full items-center justify-center py-16" aria-busy="true">
      <div className="size-10 animate-spin rounded-full border-4 border-brand-600/25 border-t-brand-600 dark:border-brand-400/20 dark:border-t-brand-400" />
    </div>
  );
}

const INITIAL_SESSION_USAGE = {
  whisperAudioSeconds: 0,
  whisperBilledMru: 0,
  whisperApiEstimatedTokensSum: 0,
  groqLessonInput: 0,
  groqLessonOutput: 0,
  groqLessonBilledMru: 0,
  mixedLangPromptTokens: 0,
  mixedLangCompletionTokens: 0,
  groqInsightPromptTokens: 0,
  groqInsightCompletionTokens: 0,
  /** MRU facturés par les appels « synthèse optionnelle » (/transcript-insight), hors total transcription. */
  groqInsightOptionalBilledMru: 0,
};

/** @param {string} ph @param {(key: string, opts?: object) => string} t */
function phaseBadgeLabels(ph, t) {
  const keys = [
    "upload",
    "transcribing",
    "editing",
    "generating",
    "lesson",
    "history",
    "credits",
    "chat",
    "notifications",
  ];
  if (keys.includes(ph)) {
    return { short: t(`phases.${ph}.short`), full: t(`phases.${ph}.full`) };
  }
  return { short: t("phases.default.short"), full: t("phases.default.full", { phase: String(ph) }) };
}

function newHistoryId() {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  } catch {
    /* noop */
  }
  return `h-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

/**
 * Fusionne les passages ASR annotés de plusieurs fichiers (indices et source_file stables pour /generate).
 * @param {{ label: string, passages: unknown }[]} pieces
 */
function mergeAsrPassagesAnnotated(pieces) {
  const out = [];
  let ix = 0;
  for (const { label, passages } of pieces) {
    if (!Array.isArray(passages)) continue;
    for (const raw of passages) {
      if (!raw || typeof raw !== "object") continue;
      const p = /** @type {Record<string, unknown>} */ (raw);
      out.push({ ...p, passage_index: ix, source_file: label });
      ix += 1;
    }
  }
  return out;
}

/** @param {unknown} v */
function normalizeLessonMarkdown(v) {
  return typeof v === "string" ? v : "";
}

/** @param {Record<string, unknown>} data Réponse brute API transcription (succès). */
function historyEntryFromTranscribePayload(data, { filenames, subject, speechLanguage, historyId, transcriptionJobPublicIds, lesson }) {
  const jobIds = Array.isArray(transcriptionJobPublicIds)
    ? transcriptionJobPublicIds.map((x) => String(x || "").trim()).filter(Boolean)
    : transcriptionJobPublicIds
      ? [String(transcriptionJobPublicIds).trim()].filter(Boolean)
      : [];
  const label =
    typeof data.filename === "string" && data.filename.trim()
      ? data.filename.trim()
      : filenames[0] || i18n.t("history.untitled");
  const mixedPieces = [{ label, view: data.transcript_mixed_view ?? null }];
  const mergedMv = mergeTranscriptMixedViews(mixedPieces);
  const transcriptJoined = `=== ${label} ===\n\n${data.timestamped_transcript || data.transcript || ""}`;
  const transcriptFinal =
    mergedMv?.blocks?.length > 0 &&
    typeof mergedMv.plain_text === "string" &&
    mergedMv.plain_text.trim()
      ? mergedMv.plain_text
      : transcriptJoined;
  const wc = transcriptFinal.trim()
    ? transcriptFinal.trim().split(/\s+/).filter(Boolean).length
    : 0;
  /** @type {Record<string, unknown>} */
  const su = typeof data.usage === "object" && data.usage !== null ? /** @type {Record<string, unknown>} */ (data.usage) : {};
  const inferred =
    typeof data.inferred_subject === "string" && data.inferred_subject.trim()
      ? data.inferred_subject.trim()
      : null;
  const subjectOut = inferred || subject;
  const ds = typeof data.deep_summary === "string" ? data.deep_summary.trim() : "";
  const ap = data.asr_passages_annotated;
  const asrPassagesAnnotated = Array.isArray(ap) ? ap : [];
  const usageSnapshot = {
    whisperAudioSeconds: Number(su.whisper_duration_seconds ?? 0),
    whisperBilledMru: Number(su.billed_mru_transcription_total ?? su.billed_mru_whisper ?? 0),
    whisperApiEstimatedTokensSum: Number(su.transcript_estimated_tokens ?? 0),
    mixedLangPromptTokens: Number(su.segment_translation_prompt_tokens ?? 0),
    mixedLangCompletionTokens: Number(su.segment_translation_completion_tokens ?? 0),
    groqLessonInput: 0,
    groqLessonOutput: 0,
    groqLessonBilledMru: 0,
    groqInsightPromptTokens: Number(su.groq_insight_prompt_tokens ?? 0),
    groqInsightCompletionTokens: Number(su.groq_insight_completion_tokens ?? 0),
    groqInsightOptionalBilledMru: 0,
  };
  return {
    id: historyId,
    createdAt: new Date().toISOString(),
    displayTitle: label,
    filenames,
    transcript: transcriptFinal,
    transcriptMixedView: mergedMv ?? null,
    subject: subjectOut,
    speechLanguage,
    language: String(data.language || i18n.t("common.dash")),
    wordCount: wc,
    durationMinutes: Number(data.duration_minutes ?? 0),
    usage: usageSnapshot,
    lesson: typeof lesson === "string" ? lesson : null,
    deepSummary: ds,
    groqInsightApplied: Boolean(data.groq_insight_applied),
    groqTranscriptTruncated: Boolean(data.groq_transcript_truncated),
    transcriptionEngine:
      typeof data.transcription_engine === "string" && data.transcription_engine.trim()
        ? data.transcription_engine.trim().toLowerCase()
        : "whisper-1",
    asrPassagesAnnotated,
    transcriptionJobPublicIds: jobIds,
  };
}

/** @param {Record<string, unknown>} row entrée liste `/api/transcribe-jobs` */
function jobListRowToLiveState(row, t) {
  const pct = typeof row.progress_percent === "number" ? row.progress_percent : 1;
  const sf = Math.min(1, Math.max(0, pct / 100));
  const msg =
    typeof row.message === "string" && row.message.trim()
      ? row.message.trim()
      : t("transcribe.serverProcessing");
  return {
    uploadFrac: 1,
    uploadFinished: true,
    serverFrac: sf,
    phase: typeof row.phase === "string" ? row.phase : "",
    message: msg,
    estimateNote: t("bgJobs.resumeNote"),
    previewText: "",
    whisperElapsedSec: null,
    whisperExpectedSec: null,
  };
}

function useDarkModeToggle() {
  const [dark, setDark] = useState(() => {
    const stored = typeof localStorage !== "undefined" && localStorage.getItem("lecturai-theme");
    if (stored) return stored === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (dark) {
      root.classList.add("dark");
      localStorage.setItem("lecturai-theme", "dark");
    } else {
      root.classList.remove("dark");
      localStorage.setItem("lecturai-theme", "light");
    }
  }, [dark]);

  return [dark, setDark];
}

function ToastPortal() {
  const [toast, setToast] = useState(null);

  useEffect(() => {
    const onToast = (e) => {
      const { msg, type } = e.detail || {};
      if (!msg) return;
      setToast({ msg, type: type || "info" });
    };
    window.addEventListener("lecturai-toast", onToast);
    return () => window.removeEventListener("lecturai-toast", onToast);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 4200);
    return () => clearTimeout(timer);
  }, [toast]);

  if (!toast) return null;
  const accent =
    toast.type === "success"
      ? "border-emerald-500/70"
      : toast.type === "error"
        ? "border-rose-500/70"
        : "border-slate-400/70";

  return (
    <div
      className={`fixed bottom-6 end-4 z-[200] flex max-w-sm items-start gap-3 rounded-2xl border px-4 py-3 text-sm shadow-soft-lg glass-panel motion-safe:animate-toast-in dark:!bg-slate-900/90 ${accent} safe-pad-x`}
    >
      <span className="text-lg">
        {toast.type === "success" ? "✅" : toast.type === "error" ? "❌" : "ℹ️"}
      </span>
      <span className="text-slate-800 dark:text-slate-100">{toast.msg}</span>
    </div>
  );
}

function AnimatedNumber({ value, className = "", suffix = "" }) {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    if (shown === value) return;
    const start = shown;
    const delta = value - start;
    if (delta === 0) return;
    const dur = Math.min(700, Math.max(180, Math.abs(delta) * 22));
    const t0 = performance.now();
    let raf;
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - k, 3);
      setShown(Math.round(start + delta * eased));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <span className={className}>
      {shown}
      {suffix}
    </span>
  );
}

function StatPill({ label, value, accent = "brand", highlight = false }) {
  const color =
    accent === "amber"
      ? "text-amber-600 dark:text-amber-400"
      : accent === "emerald"
        ? "text-emerald-600 dark:text-emerald-400"
        : "text-brand-600 dark:text-brand-400";
  return (
    <div
      className={`rounded-2xl px-3 py-2 transition ${
        highlight
          ? "bg-white/80 ring-1 ring-slate-200/80 shadow-sm dark:bg-slate-900/70 dark:ring-slate-700/80"
          : ""
      }`}
    >
      <div>
        <AnimatedNumber value={value} className={`tabular-nums text-lg font-bold ${color}`} suffix="%" />
      </div>
      <div className="text-[10px] font-normal uppercase tracking-wider opacity-80">{label}</div>
    </div>
  );
}

function TranscriptionProgressPanel({ transcriptionName, live }) {
  const { t } = useTranslation();
  const up = Math.round((live.uploadFrac ?? 0) * 100);
  const srv = Math.round((live.serverFrac ?? 0) * 100);
  const overall = Math.round(
    (TRANSCRIBE_UPLOAD_WEIGHT * ((live.uploadFinished ?? false) ? 1 : live.uploadFrac ?? 0) +
      TRANSCRIBE_SERVER_WEIGHT * (live.serverFrac ?? 0)) *
      100,
  );

  const ph = typeof live.phase === "string" ? live.phase : "";
  const postPhase =
    !!live.previewText || ph === "post_process" || ph === "whisper_complete";

  const slides = useMemo(
    () => [
      {
        icon: "📡",
        kicker: t("transcribe.step1K"),
        title: t("transcribe.step1Title"),
        body: t("transcribe.step1Body"),
      },
      {
        icon: "🎙️",
        kicker: t("transcribe.step2K"),
        title: t("transcribe.step2Title", { name: transcriptionName }),
        body: t("transcribe.step2Body", { name: transcriptionName }),
      },
      {
        icon: "⏱️",
        kicker: t("transcribe.step3K"),
        title: t("transcribe.step3Title"),
        body: t("transcribe.step3Body"),
      },
      {
        icon: "💡",
        kicker: t("transcribe.tip"),
        title: t("transcribe.step4Title"),
        body: t("transcribe.step4Body"),
      },
    ],
    [transcriptionName, t],
  );
  const tipSlideIndex = slides.length - 1;

  let activeIdx = 0;
  if (!live.uploadFinished) activeIdx = 0;
  else if (postPhase) activeIdx = 2;
  else activeIdx = 1;

  const [slide, setSlide] = useState(activeIdx);
  const [slideDir, setSlideDir] = useState(1);
  const [userTook, setUserTook] = useState(false);

  useEffect(() => {
    if (userTook) return;
    setSlideDir(activeIdx >= slide ? 1 : -1);
    setSlide(activeIdx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdx, userTook]);

  useEffect(() => {
    if (userTook) return;
    const timer = setInterval(() => {
      setSlide((cur) => (cur + 1) % slides.length);
      setSlideDir(1);
    }, 4200);
    return () => clearInterval(timer);
  }, [userTook, slides.length]);

  const goPrev = () => {
    setUserTook(true);
    setSlideDir(-1);
    setSlide((s) => (s - 1 + slides.length) % slides.length);
  };
  const goNext = () => {
    setUserTook(true);
    setSlideDir(1);
    setSlide((s) => (s + 1) % slides.length);
  };
  const jumpTo = (i) => {
    setUserTook(true);
    setSlideDir(i >= slide ? 1 : -1);
    setSlide(i);
  };

  const headline =
    (typeof live.message === "string" && live.message.trim()) || t("transcribe.defaultHeadline");
  const current = slides[slide];

  return (
    <div className="mx-auto w-full max-w-xl space-y-6">
      <div className="glass-panel relative overflow-hidden rounded-3xl p-8 text-center shadow-soft">
        <div className="pointer-events-none absolute -top-24 left-1/2 size-72 -translate-x-1/2 rounded-full bg-brand-400/20 blur-3xl motion-safe:animate-blob-drift dark:bg-brand-600/15" />
        <div className="pointer-events-none absolute -bottom-24 -right-10 size-60 rounded-full bg-amber-400/20 blur-3xl motion-safe:animate-blob-drift-reverse dark:bg-amber-600/14" />

        <div className="relative mx-auto mb-6 grid max-w-sm grid-cols-3 gap-2">
          <StatPill label={t("transcribe.networkUp")} value={up} accent="brand" />
          <StatPill label={t("transcribe.serverProc")} value={srv} accent="amber" />
          <StatPill label={t("transcribe.global")} value={overall} accent="emerald" highlight />
        </div>
        <div className="relative mx-auto flex size-32 items-center justify-center">
          <div
            className="absolute inset-0 rounded-full transition-[background] duration-500"
            style={{
              background: `conic-gradient(rgb(234 88 12) ${overall}%, rgba(148,163,184,0.18) ${overall}% 100%)`,
            }}
          />
          <div className="absolute inset-[6px] rounded-full bg-white shadow-inner dark:bg-slate-950" />
          <div className="pointer-events-none absolute inset-0 rounded-full border border-brand-400/40 motion-safe:animate-ping" />
          <div
            key={current.icon}
            className="relative text-3xl motion-safe:animate-fade-in-up"
            aria-hidden="true"
          >
            {current.icon}
          </div>
        </div>
        <div className="relative mt-3 text-xs text-slate-500 dark:text-slate-400">
          <AnimatedNumber value={overall} className="tabular-nums font-semibold" suffix="%" /> {t("transcribe.donePct")}
        </div>
        <h2 className="relative mt-4 font-display text-xl font-bold text-slate-900 dark:text-white">
          {headline}
        </h2>
        {typeof live.estimateNote === "string" && live.estimateNote.trim() ? (
          <p className="relative mx-auto mt-2 max-w-md text-[11px] leading-relaxed text-slate-500 dark:text-slate-500">
            {live.estimateNote}
          </p>
        ) : null}

        <div className="relative mx-auto mt-6 max-w-md">
          <div className="relative h-[124px] overflow-hidden rounded-2xl border border-slate-200/80 bg-white/70 shadow-inner dark:border-slate-800/80 dark:bg-slate-950/40">
            <button
              type="button"
              aria-label={t("transcribe.prev")}
              onClick={goPrev}
              className="absolute left-2 top-1/2 z-10 flex size-7 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-slate-600 shadow ring-1 ring-slate-200 transition hover:bg-white hover:text-brand-600 dark:bg-slate-900/90 dark:text-slate-300 dark:ring-slate-700 dark:hover:text-brand-400"
            >
              <span aria-hidden="true" className="-mt-0.5 text-base leading-none">‹</span>
            </button>
            <button
              type="button"
              aria-label={t("transcribe.next")}
              onClick={goNext}
              className="absolute right-2 top-1/2 z-10 flex size-7 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-slate-600 shadow ring-1 ring-slate-200 transition hover:bg-white hover:text-brand-600 dark:bg-slate-900/90 dark:text-slate-300 dark:ring-slate-700 dark:hover:text-brand-400"
            >
              <span aria-hidden="true" className="-mt-0.5 text-base leading-none">›</span>
            </button>
            <div
              key={`${slide}-${slideDir}`}
              className={`absolute inset-0 flex flex-col justify-center px-12 text-start ${
                slideDir > 0
                  ? "motion-safe:animate-slide-in-right"
                  : "motion-safe:animate-slide-in-left"
              }`}
            >
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-brand-600 dark:text-brand-400">
                <span>{current.kicker}</span>
                {slide === activeIdx && slide !== tipSlideIndex ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-brand-100 px-2 py-0.5 text-[9px] font-bold text-brand-700 dark:bg-brand-900/60 dark:text-brand-200">
                    <span className="size-1.5 rounded-full bg-brand-500 motion-safe:animate-pulse" />
                    {t("transcribe.inProgress")}
                  </span>
                ) : slide < activeIdx && slide !== tipSlideIndex ? (
                  <span className="text-emerald-600 dark:text-emerald-400">{t("transcribe.completed")}</span>
                ) : null}
              </div>
              <div className="mt-1 font-display text-sm font-bold text-slate-900 dark:text-white">
                {current.title}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">
                {current.body}
              </p>
            </div>
          </div>

          <div className="mt-3 flex items-center justify-center gap-2">
            {slides.map((s, i) => (
              <button
                key={i}
                type="button"
                aria-label={t("transcribe.slideToAria", { title: s.title })}
                onClick={() => jumpTo(i)}
                className={`h-1.5 rounded-full transition-all ${
                  i === slide
                    ? "w-6 bg-brand-600 dark:bg-brand-400"
                    : i === activeIdx
                      ? "w-3 bg-brand-300 motion-safe:animate-pulse dark:bg-brand-700"
                      : "w-3 bg-slate-300 hover:bg-slate-400 dark:bg-slate-700 dark:hover:bg-slate-600"
                }`}
              />
            ))}
          </div>
        </div>

        {(typeof live.whisperElapsedSec === "number" || typeof live.whisperExpectedSec === "number") && ph ===
        "whisper" ? (
          <p className="relative mt-5 text-xs text-slate-500 dark:text-slate-400">
            {t("transcribe.whisperTimingLead")}
            {live.whisperElapsedSec != null ? t("transcribe.elapsedOnly", { s: Math.round(live.whisperElapsedSec) }) : "…"}
            {live.whisperExpectedSec != null ? t("transcribe.expectedPart", { s: Math.round(live.whisperExpectedSec) }) : null}
          </p>
        ) : null}

        {live.previewText ? (
          <div className="relative mx-auto mt-6 max-h-48 overflow-y-auto rounded-2xl border border-slate-200/90 bg-white/75 p-4 text-start text-xs leading-relaxed text-slate-800 shadow-inner dark:border-slate-700 dark:bg-slate-950/50 dark:text-slate-100">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">
              {t("transcribe.previewRaw")}
            </div>
            <p className="whitespace-pre-wrap">{live.previewText}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function GeneratingSkeleton() {
  const { t } = useTranslation();
  const generationSteps = useMemo(
    () => [t("generating.s1"), t("generating.s2"), t("generating.s3"), t("generating.s4")],
    [t],
  );
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setIdx((i) => (i + 1) % generationSteps.length), 1400);
    return () => clearInterval(timer);
  }, [generationSteps.length, t]);

  return (
    <div className="mx-auto w-full max-w-xl space-y-6">
      <div className="space-y-2">
        <div className="h-4 w-56 animate-pulse rounded-lg bg-slate-200 dark:bg-slate-700" />
        <div className="h-3 max-w-md animate-pulse rounded-lg bg-slate-200 dark:bg-slate-700" />
      </div>
      <div className="relative overflow-hidden rounded-3xl border border-brand-200/80 bg-gradient-to-br from-brand-50 via-white to-amber-50/50 p-8 text-center shadow-soft dark:border-brand-900/50 dark:from-slate-950 dark:via-slate-900 dark:to-amber-950/25">
        <div className="pointer-events-none absolute -right-20 -top-20 h-40 w-40 rounded-full bg-brand-400/20 blur-3xl dark:bg-brand-600/10" />
        <div className="relative text-4xl">✨</div>
        <h2 className="relative font-display text-xl font-bold text-slate-900 dark:text-white">
          {t("generating.title")}
        </h2>
        <p className="relative mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-400">
          {t("generating.subtitle", { course: ENGINE_COURSE })}
        </p>
        <div className="mx-auto mt-4 h-2 max-w-xs overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
          <div
            className="h-full rounded-full bg-gradient-to-r from-brand-600 via-amber-500 to-rose-400 transition-[width] duration-500"
            style={{ width: `${25 + (idx + 1) * 18}%` }}
          />
        </div>
        <div className="relative mt-6 space-y-3 text-start text-sm text-slate-700 dark:text-slate-300">
          {generationSteps.map((label, i) => (
            <div key={i} className="flex items-center gap-3">
              <div
                className={`flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                  i === idx ? "bg-brand-600 text-white shadow-glow" : "bg-slate-200 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                }`}
              >
                {i + 1}
              </div>
              <span className={i === idx ? "font-semibold text-slate-900 dark:text-white" : ""}>{label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const { t, i18n } = useTranslation();
  const [dark, setDark] = useDarkModeToggle();
  // Détection au mount de la route publique `/c/<token>` — court-circuite tout le pipeline auth.
  // Volontairement calculé une seule fois : changer de leçon = full reload (URL différente).
  const [publicShareToken] = useState(() => publicShareTokenFromLocation());
  // Capture `?ref=CODE` dans l'URL et le persiste pour l'inscription suivante (idempotent).
  useEffect(() => {
    captureReferralFromUrl();
  }, []);
  const [authGate, setAuthGate] = useState({
    loading: true,
    skipped: false,
    ready: false,
    loadError: false,
  });

  const [profileFlags, setProfileFlags] = useState(() => ({
    is_admin: Boolean(getAuthProfile()?.is_admin),
  }));

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const r = await fetch(apiUrl("/api/auth/config"));
        if (!r.ok) throw new Error("config");
        const d = await r.json();
        if (cancel) return;
        if (!d.auth_required) {
          setProfileFlags({ is_admin: false });
          setAuthGate({ loading: false, skipped: true, ready: true, loadError: false });
          return;
        }
        const tok = getAuthToken();
        if (!tok) {
          setAuthGate({ loading: false, skipped: false, ready: false, loadError: false });
          return;
        }
        const mr = await fetch(apiUrl("/api/auth/me"), { headers: getAuthHeaders(false) });
        if (cancel) return;
        if (mr.ok) {
          const p = await parseJsonResponse(mr);
          if (cancel) return;
          if (!p.ok || !p.data?.user) {
            setProfileFlags({ is_admin: false });
          } else {
            const u = p.data.user;
            const tokNow = getAuthToken();
            if (tokNow) {
              setAuthSession(tokNow, { ...getAuthProfile(), ...u });
              setProfileFlags({ is_admin: Boolean(u.is_admin) });
            } else setProfileFlags({ is_admin: false });
          }
          setAuthGate({ loading: false, skipped: false, ready: true, loadError: false });
        } else {
          clearAuthSession();
          setProfileFlags({ is_admin: false });
          setAuthGate({ loading: false, skipped: false, ready: false, loadError: false });
        }
      } catch {
        if (!cancel) {
          setProfileFlags({ is_admin: false });
          setAuthGate({ loading: false, skipped: false, ready: false, loadError: true });
        }
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  /** @param {undefined | Record<string, unknown>} authPayload Réponse brute login/inscription (`user` inclus). */
  const handleAuthed = (authPayload) => {
    const u = authPayload?.user ?? getAuthProfile();
    setProfileFlags({ is_admin: Boolean(u?.is_admin) });
    setAuthGate({ loading: false, skipped: false, ready: true, loadError: false });
  };

  /** @typedef {{ balance: number; canUse: boolean; blocked: string | null; expireAt: string | null } | null} CreditHud */

  /** @type {[CreditHud, (v: CreditHud) => void]} */
  const [creditHud, setCreditHud] = useState(null);

  const reloadCreditHud = useCallback(async () => {
    if (!authGate.ready || authGate.skipped) {
      setCreditHud(null);
      return;
    }
    if (profileFlags.is_admin) {
      setCreditHud(null);
      return;
    }
    try {
      const res = await fetch(apiUrl("/api/credits/me"), { headers: getAuthHeaders(false) });
      const p = await parseJsonResponse(res);
      if (!p.ok || p.data.feature_enabled === false) {
        setCreditHud(null);
        return;
      }
      setCreditHud({
        balance: typeof p.data.balance_mru === "number" ? Number(p.data.balance_mru) : 0,
        canUse: p.data.can_use_features !== false,
        blocked: p.data.block_reason ? String(p.data.block_reason) : null,
        expireAt: p.data.credits_expire_at ? String(p.data.credits_expire_at) : null,
      });
    } catch {
      setCreditHud(null);
    }
  }, [authGate.ready, authGate.skipped, profileFlags.is_admin]);

  useEffect(() => {
    void reloadCreditHud();
  }, [reloadCreditHud]);

  const [notifUnread, setNotifUnread] = useState(0);
  const reloadNotifUnread = useCallback(async () => {
    if (!authGate.ready || authGate.skipped) {
      setNotifUnread(0);
      return;
    }
    if (profileFlags.is_admin) {
      setNotifUnread(0);
      return;
    }
    try {
      const res = await fetch(apiUrl("/api/notifications/unread-count"), {
        headers: getAuthHeaders(false),
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) {
        setNotifUnread(0);
        return;
      }
      setNotifUnread(Math.max(0, Number(p.data?.unread || 0)));
    } catch {
      setNotifUnread(0);
    }
  }, [authGate.ready, authGate.skipped, profileFlags.is_admin]);

  useEffect(() => {
    void reloadNotifUnread();
    if (!authGate.ready || authGate.skipped || profileFlags.is_admin) return undefined;
    const iv = setInterval(() => void reloadNotifUnread(), 30000);
    return () => clearInterval(iv);
  }, [reloadNotifUnread, authGate.ready, authGate.skipped, profileFlags.is_admin]);

  const [phase, setPhase] = useState("upload");
  const [files, setFiles] = useState([]);
  const [subject, setSubject] = useState("");
  const [speechLanguage, setSpeechLanguage] = useState("fr");
  const [transcriptionEngine, setTranscriptionEngine] = useState(() => "whisper-1");
  const [transcript, setTranscript] = useState("");
  const [transcriptMixedView, setTranscriptMixedView] = useState(null);
  /** Passages ASR + scores (réponse transcription) — obligatoire pour /generate avec collage JSON. */
  const [asrPassagesAnnotated, setAsrPassagesAnnotated] = useState(/** @type {unknown[]} */ ([]));
  const [language, setLanguage] = useState("");
  const [wordCount, setWordCount] = useState(0);
  const [durationMinutes, setDurationMinutes] = useState(0);
  const [primaryName, setPrimaryName] = useState("");
  const [lesson, setLesson] = useState("");
  /** Résumé approfondi (LecturaSynth) après transcription ; conservé avec l'historique. */
  const [deepSummary, setDeepSummary] = useState("");
  const [groqTruncatedHint, setGroqTruncatedHint] = useState(false);
  const [groqInsightApplied, setGroqInsightApplied] = useState(false);
  /** Panneau synthèse d’aide à la lecture : ouvert seulement si demandé ou session historique avec synthèse. */
  const [insightPanelOpen, setInsightPanelOpen] = useState(false);
  const [insightLoading, setInsightLoading] = useState(false);
  const [activeTab, setActiveTab] = useState("lesson");
  const [batchProgress, setBatchProgress] = useState({ perFile: [], overallPct: 0 });
  const [transcribeLive, setTranscribeLive] = useState(null);
  const [celebrate, setCelebrate] = useState(false);
  /** Remonte l’ErrorBoundary autour du cours après chaque génération / ouverture session. */
  const [lessonViewBoundaryKey, setLessonViewBoundaryKey] = useState(0);
  const [sessionUsage, setSessionUsage] = useState(() => ({ ...INITIAL_SESSION_USAGE }));
  const [historyBump, reloadHistoryList] = useReducer((x) => x + 1, 0);
  const [currentHistoryId, setCurrentHistoryId] = useState(null);
  const [currentJobPublicIds, setCurrentJobPublicIds] = useState([]);

  const historyItems = useMemo(() => loadHistory(), [historyBump]);
  const badges = useMemo(() => phaseBadgeLabels(phase, t), [phase, t]);

  const [transcriptionRatingOffer, setTranscriptionRatingOffer] = useState(/** @type {{ jobPublicIds: string[]; label: string } | null} */ (null));
  const [transcriptionRatingModalOpen, setTranscriptionRatingModalOpen] = useState(false);
  const [ideaFeedbackOpen, setIdeaFeedbackOpen] = useState(false);

  const queueTranscriptionRatingOffer = useCallback((jobPublicIds, label) => {
    const ids = [...new Set((jobPublicIds || []).map((x) => String(x || "").trim()).filter(Boolean))];
    if (!ids.length) return;
    if (wasRatingPromptHandled(ids)) return;
    setTranscriptionRatingOffer({ jobPublicIds: ids, label: label || "" });
    setTranscriptionRatingModalOpen(false);
  }, []);

  const showTranscriptionRatingCTA = useMemo(() => {
    if (!transcriptionRatingOffer || transcriptionRatingModalOpen) return false;
    if (wasRatingPromptHandled(transcriptionRatingOffer.jobPublicIds)) return false;
    if (!currentHistoryId) return false;
    const entry = getEntry(currentHistoryId);
    const entryIds = Array.isArray(entry?.transcriptionJobPublicIds) ? entry.transcriptionJobPublicIds : [];
    if (!entryIds.length) return false;
    return (
      ratingPromptSignature(entryIds) === ratingPromptSignature(transcriptionRatingOffer.jobPublicIds)
    );
  }, [transcriptionRatingOffer, transcriptionRatingModalOpen, currentHistoryId]);

  useEffect(() => {
    document.title = t("meta.title");
  }, [t]);

  useEffect(() => {
    if (!authGate.ready) return undefined;
    if (!getAuthToken()) return undefined;

    let cancelled = false;

    async function reconcilePendingBgJobs() {
      const ids = loadBgTranscribeJobIds();
      if (!ids.length) return;

      for (const jobId of ids) {
        if (cancelled) return;
        try {
          const resLite = await fetch(
            `${apiUrl(`/api/transcribe-jobs/${encodeURIComponent(jobId)}`)}?include_result=false`,
            { headers: getAuthHeaders(false) },
          );
          const p = await parseJsonResponse(resLite);
          if (!p.ok || typeof p.data !== "object" || p.data === null) continue;
          /** @type {Record<string, unknown>} */
          const lite = /** @type {Record<string, unknown>} */ (p.data);
          const stLite = String(lite.status || "");
          if (stLite === "failed" || stLite === "cancelled") {
            forgetBgTranscribeJobId(jobId);
            void reloadCreditHud();
            continue;
          }
          if (stLite !== "done") continue;

          const hid = `bg-job-${jobId}`;
          if (loadHistory().some((e) => e.id === hid)) {
            forgetBgTranscribeJobId(jobId);
            continue;
          }

          const resFull = await fetch(
            `${apiUrl(`/api/transcribe-jobs/${encodeURIComponent(jobId)}`)}?include_result=true`,
            { headers: getAuthHeaders(false) },
          );
          const pf = await parseJsonResponse(resFull);
          if (!pf.ok || typeof pf.data !== "object" || pf.data === null) continue;
          /** @type {Record<string, unknown>} */
          const full = /** @type {Record<string, unknown>} */ (pf.data);
          const rawRes = full.result;
          if (!rawRes || typeof rawRes !== "object") continue;
          /** @type {Record<string, unknown>} */
          const result = /** @type {Record<string, unknown>} */ (rawRes);

          const fn =
            typeof result.filename === "string" && result.filename.trim()
              ? result.filename.trim()
              : typeof full.original_filename === "string"
                ? full.original_filename
                : i18n.t("common.audio");

          prependEntry(
            historyEntryFromTranscribePayload(result, {
              historyId: hid,
              filenames: [fn],
              subject: typeof full.subject === "string" ? full.subject : i18n.t("common.general"),
              speechLanguage: full.speech_language === "ar" ? "ar" : "fr",
              transcriptionJobPublicIds: [jobId],
            }),
          );
          forgetBgTranscribeJobId(jobId);
          reloadHistoryList();
          void reloadCreditHud();
          queueTranscriptionRatingOffer([jobId], fn);
          window.dispatchEvent(
            new CustomEvent("lecturai-toast", {
              detail: { msg: t("app.toastBgReady", { name: fn }), type: "success" },
            }),
          );
        } catch {
          /* réseau : retenter au prochain intervalle */
        }
      }
    }

    void reconcilePendingBgJobs();
    const iv = setInterval(() => void reconcilePendingBgJobs(), 9000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [authGate.ready, reloadCreditHud, t, queueTranscriptionRatingOffer]);

  const busy = phase === "transcribing" || phase === "generating";

  const bgResumeAbortRef = useRef(/** @type {AbortController | null} */ (null));

  const goUpload = () => {
    bgResumeAbortRef.current?.abort();
    bgResumeAbortRef.current = null;
    setPhase("upload");
    setSpeechLanguage("fr");
    setTranscriptionEngine("openai");
    setFiles([]);
    setBatchProgress({ perFile: [], overallPct: 0 });
    setTranscribeLive(null);
    setSessionUsage({ ...INITIAL_SESSION_USAGE });
    setTranscriptMixedView(null);
    setAsrPassagesAnnotated([]);
    setDeepSummary("");
    setGroqTruncatedHint(false);
    setGroqInsightApplied(false);
    setInsightPanelOpen(false);
    setInsightLoading(false);
    setCurrentHistoryId(null);
    setCurrentJobPublicIds([]);
    setTranscriptionRatingOffer(null);
    setTranscriptionRatingModalOpen(false);
  };

  const openHistoryEntry = (entry) => {
    if (!entry?.id) return;
    const u = entry.usage || {};
    setTranscript(entry.transcript || "");
    setSubject(entry.subject || "");
    setSpeechLanguage(entry.speechLanguage === "ar" ? "ar" : "fr");
    setTranscriptionEngine(
      typeof entry.transcriptionEngine === "string" && entry.transcriptionEngine.trim()
        ? entry.transcriptionEngine.trim().toLowerCase()
        : "whisper-1",
    );
    setLanguage(entry.language || "");
    setWordCount(entry.wordCount || 0);
    setDurationMinutes(entry.durationMinutes ?? 0);
    setPrimaryName(entry.displayTitle || "");
    let lessonToUse = normalizeLessonMarkdown(entry.lesson);
    if (!lessonToUse.trim() && Array.isArray(entry.transcriptionJobPublicIds) && entry.transcriptionJobPublicIds.length > 0) {
      const jid = entry.transcriptionJobPublicIds[0];
      void (async () => {
        try {
          const full = await getTranscriptionJob(jid, { include_result: false });
          if (full.lesson && typeof full.lesson === "string" && full.lesson.trim()) {
            const normalized = normalizeLessonMarkdown(full.lesson);
            setLesson(normalized);
            updateEntry(entry.id, { lesson: normalized });
            setLessonViewBoundaryKey((k) => k + 1);
            if (phase === "editing" || phase === "lesson") {
               setPhase("lesson");
               setActiveTab("lesson");
            }
          }
        } catch (e) {
          logger.error("Failed to fetch lesson from job %s", jid, e);
        }
      })();
    }
    setLesson(lessonToUse);
    setTranscriptMixedView(entry.transcriptMixedView ?? null);
    setAsrPassagesAnnotated(Array.isArray(entry.asrPassagesAnnotated) ? entry.asrPassagesAnnotated : []);
    setDeepSummary(entry.deepSummary || "");
    setGroqTruncatedHint(Boolean(entry.groqTranscriptTruncated));
    setGroqInsightApplied(Boolean(entry.groqInsightApplied));
    setInsightPanelOpen(Boolean(String(entry.deepSummary || "").trim()));
    setInsightLoading(false);
    setSessionUsage({
      whisperAudioSeconds: u.whisperAudioSeconds ?? 0,
      whisperBilledMru: u.whisperBilledMru ?? 0,
      whisperApiEstimatedTokensSum: u.whisperApiEstimatedTokensSum ?? 0,
      groqLessonInput: u.groqLessonInput ?? u.claudeInput ?? 0,
      groqLessonOutput: u.groqLessonOutput ?? u.claudeOutput ?? 0,
      groqLessonBilledMru: u.groqLessonBilledMru ?? u.claudeBilledMru ?? 0,
      mixedLangPromptTokens: u.mixedLangPromptTokens ?? 0,
      mixedLangCompletionTokens: u.mixedLangCompletionTokens ?? 0,
      groqInsightPromptTokens: u.groqInsightPromptTokens ?? 0,
      groqInsightCompletionTokens: u.groqInsightCompletionTokens ?? 0,
      groqInsightOptionalBilledMru: Number(u.groqInsightOptionalBilledMru ?? 0),
    });
    setCurrentHistoryId(entry.id);
    setCurrentJobPublicIds(Array.isArray(entry.transcriptionJobPublicIds) ? entry.transcriptionJobPublicIds : []);
    const hasLesson = Boolean(normalizeLessonMarkdown(entry.lesson).trim());
    setActiveTab(hasLesson ? "lesson" : "transcript");
    setPhase(hasLesson ? "lesson" : "editing");
    if (hasLesson) setLessonViewBoundaryKey((k) => k + 1);
    window.dispatchEvent(
      new CustomEvent("lecturai-toast", { detail: { msg: t("app.sessionRestored"), type: "info" } }),
    );
  };

  const openBgJobFromList = async (/** @type {Record<string, unknown>} */ row) => {
      if (busy) {
        window.dispatchEvent(
          new CustomEvent("lecturai-toast", {
            detail: { msg: t("bgJobs.busyOpenBlocked"), type: "info" },
          }),
        );
        return;
      }
      const jobId =
        typeof row.job_id === "string" ? row.job_id : typeof row.job_id === "number" ? String(row.job_id) : "";
      if (!jobId.trim()) return;

      const st = String(row.status || "");

      if (st === "cancelled") {
        window.dispatchEvent(
          new CustomEvent("lecturai-toast", {
            detail: { msg: t("bgJobs.openCancelled"), type: "info" },
          }),
        );
        return;
      }

      if (st === "failed") {
        window.dispatchEvent(
          new CustomEvent("lecturai-toast", {
            detail: {
              msg: userFacingTranscriptionJobFailure(
                typeof row.error_detail === "string" ? row.error_detail : "",
                typeof row.message === "string" ? row.message : "",
                t,
              ),
              type: "error",
            },
          }),
        );
        return;
      }

      if (st === "done") {
        try {
          const hid = `bg-job-${jobId}`;
          let entry = loadHistory().find((e) => e.id === hid);
          if (!entry) {
            const full = await getTranscriptionJob(jobId, { include_result: true });
            const rawRes = full.result;
            if (!rawRes || typeof rawRes !== "object") {
              throw new Error(t("transcribe.transcribeEmpty"));
            }
            /** @type {Record<string, unknown>} */
            const result = rawRes;
            const fn =
              typeof result.filename === "string" && result.filename.trim()
                ? result.filename.trim()
                : typeof full.original_filename === "string"
                  ? full.original_filename
                  : t("common.audio");
            prependEntry(
              historyEntryFromTranscribePayload(result, {
                historyId: hid,
                filenames: [fn],
                subject: typeof full.subject === "string" ? full.subject : t("common.general"),
                speechLanguage: full.speech_language === "ar" ? "ar" : "fr",
                transcriptionJobPublicIds: [jobId],
                lesson: full.lesson,
              }),
            );
            forgetBgTranscribeJobId(jobId);
            reloadHistoryList();
            void reloadCreditHud();
            queueTranscriptionRatingOffer([jobId], fn);
            entry = loadHistory().find((e) => e.id === hid);
          }
          if (entry) openHistoryEntry(entry);
        } catch (e) {
          window.dispatchEvent(
            new CustomEvent("lecturai-toast", {
              detail: { msg: e?.message || t("bgJobs.openDoneFail"), type: "error" },
            }),
          );
        }
        return;
      }

      bgResumeAbortRef.current?.abort();
      const ac = new AbortController();
      bgResumeAbortRef.current = ac;
      persistBgTranscribeJobId(jobId);

      const displayName =
        typeof row.original_filename === "string" && row.original_filename.trim()
          ? row.original_filename.trim()
          : t("history.untitled");

      setFiles([]);
      setBatchProgress({ perFile: [], overallPct: 0 });
      setPrimaryName(displayName);
      setTranscript("");
      setTranscriptMixedView(null);
      setAsrPassagesAnnotated([]);
      setTranscribeLive(jobListRowToLiveState(row, t));
      setPhase("transcribing");

      const bumpFromPollRow = (/** @type {Record<string, unknown>} */ pollingRow) => {
        const p = typeof pollingRow.progress_percent === "number" ? pollingRow.progress_percent : 1;
        setTranscribeLive((prev) => ({
          ...(prev || {}),
          uploadFrac: 1,
          uploadFinished: true,
          serverFrac: Math.min(1, Math.max(0, p / 100)),
          phase:
            typeof pollingRow.phase === "string" ? pollingRow.phase : typeof prev?.phase === "string" ? prev.phase : "",
          message:
            typeof pollingRow.message === "string" && pollingRow.message.trim()
              ? pollingRow.message.trim()
              : prev?.message || t("transcribe.serverProcessing"),
          previewText: prev?.previewText || "",
          estimateNote: t("bgJobs.resumeNote"),
          whisperElapsedSec: prev?.whisperElapsedSec ?? null,
          whisperExpectedSec: prev?.whisperExpectedSec ?? null,
        }));
      };

      void (async () => {
        try {
          /** @type {Record<string, unknown>} */
          const terminalRow = await waitForTerminalTranscriptionJob(jobId, {
            signal: ac.signal,
            onTick: bumpFromPollRow,
          });

          forgetBgTranscribeJobId(jobId);
          if (bgResumeAbortRef.current === ac) bgResumeAbortRef.current = null;

          /** @type {unknown} */
          const rawResult = terminalRow.result;
          if (!rawResult || typeof rawResult !== "object") {
            throw new Error(t("transcribe.transcribeEmpty"));
          }
          /** @type {Record<string, unknown>} */
          const data = rawResult;

          const label =
            typeof data.filename === "string" && data.filename.trim()
              ? data.filename.trim()
              : typeof terminalRow.original_filename === "string"
                ? terminalRow.original_filename.trim()
                : displayName;

          const hid = `bg-job-${jobId}`;
          prependEntry(
            historyEntryFromTranscribePayload(data, {
              historyId: hid,
              filenames: [label],
              subject:
                typeof terminalRow.subject === "string" ? terminalRow.subject.trim() || t("common.general") : t("common.general"),
              speechLanguage: terminalRow.speech_language === "ar" ? "ar" : "fr",
              transcriptionJobPublicIds: [jobId],
              lesson: terminalRow.lesson,
            }),
          );
          reloadHistoryList();
          const settled = loadHistory().find((e) => e.id === hid);
          if (settled) openHistoryEntry(settled);

          void reloadCreditHud();
          queueTranscriptionRatingOffer([jobId], label);
          window.dispatchEvent(
            new CustomEvent("lecturai-toast", {
              detail: { msg: t("app.transcriptReady"), type: "success" },
            }),
          );
        } catch (e) {
          const aborted =
            ac.signal.aborted ||
            /** @type {{ name?: string; message?: string }} */ (e)?.name === "AbortError" ||
            /** @type {{ message?: string }} */ (e)?.message === "Interrompu.";
          forgetBgTranscribeJobId(jobId);
          if (bgResumeAbortRef.current === ac) bgResumeAbortRef.current = null;
          if (aborted) {
            setPhase((p) => (p === "transcribing" ? "upload" : p));
            return;
          }
          if (e instanceof TranscriptionJobFailedError) {
            window.dispatchEvent(
              new CustomEvent("lecturai-toast", {
                detail: {
                  msg: e.message || userFacingTranscriptionJobFailure("", "", t),
                  type: "error",
                },
              }),
            );
            void reloadCreditHud();
            setPhase("upload");
            return;
          }
          const msg =
            e instanceof Error && e.message.trim()
              ? e.message.trim()
              : t("app.transcribeFail");
          window.dispatchEvent(
            new CustomEvent("lecturai-toast", {
              detail: { msg, type: "error" },
            }),
          );
          void reloadCreditHud();
          setPhase("upload");
        }
      })();
    };

  const deleteHistoryEntry = (id) => {
    removeEntry(id);
    reloadHistoryList();
    if (id === currentHistoryId) setCurrentHistoryId(null);
    window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg: t("app.entryDeleted"), type: "success" } }));
  };

  useEffect(() => {
    if (phase !== "lesson") {
      setCelebrate(false);
      return;
    }
    setCelebrate(true);
    const t = setTimeout(() => setCelebrate(false), 3200);
    return () => clearTimeout(t);
  }, [phase]);

  useEffect(() => {
    if (phase !== "editing" || !currentHistoryId) return;
    const tmr = setTimeout(() => {
      const wc = transcript.trim()
        ? transcript.trim().split(/\s+/).filter(Boolean).length
        : 0;
      updateEntry(currentHistoryId, {
        transcript,
        wordCount: wc,
        subject,
        deepSummary,
        groqInsightApplied,
        groqTranscriptTruncated: groqTruncatedHint,
        asrPassagesAnnotated,
      });
      setWordCount(wc);
    }, 1400);
    return () => clearTimeout(tmr);
  }, [transcript, subject, phase, currentHistoryId, deepSummary, groqInsightApplied, groqTruncatedHint, asrPassagesAnnotated]);

  const exportBaseName = useMemo(() => {
    if (primaryName) return primaryName.replace(/\.[^/.]+$/, "");
    if (files[0]?.name) return files[0].name.replace(/\.[^/.]+$/, "");
    return i18n.t("common.lecture");
  }, [primaryName, files]);

  const startTranscription = async () => {
    if (files.length === 0) return;
    bgResumeAbortRef.current?.abort();
    bgResumeAbortRef.current = null;
    setPhase("transcribing");
    const n = files.length;
    const perFile = Array(n).fill(0);
    setBatchProgress({ perFile: [...perFile], overallPct: 0 });
    setTranscribeLive({
      uploadFrac: 0,
      uploadFinished: false,
      serverFrac: 0,
      phase: "",
      message: t("transcribe.readySend", {
        what: n > 1 ? t("transcribe.filesWord") : t("transcribe.fileWord"),
      }),
      estimateNote: "",
      previewText: "",
      whisperElapsedSec: null,
      whisperExpectedSec: null,
    });

    const bump = () => {
      const sum = perFile.reduce((a, b) => a + b, 0);
      setBatchProgress({ perFile: [...perFile], overallPct: sum / n });
    };

    try {
      const jobPublicIdsCollected = [];
      setSessionUsage({ ...INITIAL_SESSION_USAGE });
      setTranscriptMixedView(null);
      setAsrPassagesAnnotated([]);
      let wSec = 0;
      let wMru = 0;
      let wTok = 0;
      let mixPrompt = 0;
      let mixComp = 0;
      const chunks = [];
      const mixedPieces = [];
      let langs = [];
      let words = 0;
      let minutes = 0;

      let groqPromptTot = 0;
      let groqCompTot = 0;
      let groqTruncAny = false;
      let groqAppliedFlag = false;
      let inferredMerged = "";
      /** @type {string[]} */
      const summaryPieces = [];
      /** @type {{ label: string, passages: unknown }[]} */
      const asrPieces = [];

      /** @type {Record<string, unknown>} */
      let data;
      for (let i = 0; i < n; i += 1) {
        const f = files[i];
        setTranscribeLive((prev) => ({
          ...(prev || {}),
          previewText: "",
          phase: "",
          serverFrac: 0,
          message: t("transcribe.prepSend", {
            i: i + 1,
            n,
            extra: n > 1 ? t("transcribe.prepName", { name: f.name }) : "",
          }),
        }));
        let uf = 0;
        let ufDone = false;
        let sf = 0;
        let jobIdCaptured = "";

        /** @param {Record<string, unknown>} row */
        const bumpFromPollRow = (row) => {
          if (typeof row.progress_percent === "number") {
            sf = Math.min(1, Math.max(0, row.progress_percent / 100));
          }
          setTranscribeLive((prev) => ({
            ...(prev || {}),
            uploadFrac: uf,
            uploadFinished: ufDone,
            serverFrac: sf,
            phase: typeof row.phase === "string" ? row.phase : prev?.phase ?? "",
            message:
              typeof row.message === "string" && row.message.trim()
                ? row.message
                : prev?.message ?? t("transcribe.serverProcessing"),
            previewText: prev?.previewText || "",
            whisperElapsedSec: null,
            whisperExpectedSec: null,
          }));
          syncRowProgress();
        };

        const syncRowProgress = () => {
          const m =
            TRANSCRIBE_UPLOAD_WEIGHT * (ufDone ? 1 : uf) + TRANSCRIBE_SERVER_WEIGHT * sf;
          perFile[i] = m;
          bump();
        };

        syncRowProgress();

        try {
          const enq = await enqueueTranscribeJobWithXHR(f, subject, speechLanguage, {
            transcriptionEngine,
            uiLocale: i18n.language,
            onUploadProgress: (u) => {
              uf = u;
              setTranscribeLive((prev) => ({
                ...(prev || {}),
                uploadFrac: uf,
                uploadFinished: ufDone,
                serverFrac: sf,
              }));
              syncRowProgress();
            },
            onUploadComplete: () => {
              ufDone = true;
              uf = 1;
              setTranscribeLive((prev) => ({
                ...(prev || {}),
                uploadFrac: 1,
                uploadFinished: true,
                serverFrac: sf,
                message: prev?.message || t("transcribe.analyzing"),
              }));
              syncRowProgress();
            },
          });
          jobIdCaptured = enq.job_id;
          persistBgTranscribeJobId(jobIdCaptured);

          const terminalRow = await waitForTerminalTranscriptionJob(enq.job_id, {
            onTick: bumpFromPollRow,
          });

          forgetBgTranscribeJobId(enq.job_id);

          /** @type {unknown} */
          const rawResult = terminalRow.result;
          if (!rawResult || typeof rawResult !== "object") {
            throw new Error(t("transcribe.transcribeEmpty"));
          }
          data = /** @type {Record<string, unknown>} */ (rawResult);
          jobPublicIdsCollected.push(enq.job_id);
        } catch (fileErr) {
          if (
            jobIdCaptured &&
            (fileErr instanceof TranscriptionJobFailedError ||
              /** @type {{ name?: string }} */ (fileErr)?.name === "TranscriptionJobFailedError")
          ) {
            forgetBgTranscribeJobId(jobIdCaptured);
          }
          throw fileErr;
        }
        perFile[i] = 1;
        uf = 1;
        ufDone = true;
        sf = 1;
        setTranscribeLive((prev) => ({
          ...(prev || {}),
          uploadFrac: 1,
          uploadFinished: true,
          serverFrac: 1,
          message:
            i + 1 < n ? t("transcribe.blockDone", { i: i + 1, n }) : t("transcribe.transcribeDone"),
          whisperElapsedSec: null,
          whisperExpectedSec: null,
        }));
        bump();

        const su = data.usage || {};
        wSec += Number(su.whisper_duration_seconds ?? 0);
        wMru += Number(su.billed_mru_transcription_total ?? su.billed_mru_whisper ?? 0);
        wTok += Number(su.transcript_estimated_tokens ?? 0);
        mixPrompt += Number(su.segment_translation_prompt_tokens ?? 0);
        mixComp += Number(su.segment_translation_completion_tokens ?? 0);
        groqPromptTot += Number(su.groq_insight_prompt_tokens ?? 0);
        groqCompTot += Number(su.groq_insight_completion_tokens ?? 0);
        
        // Capture des infos moteur / tarif

        if (data.groq_transcript_truncated) groqTruncAny = true;
        if (data.groq_insight_applied) groqAppliedFlag = true;
        const inferred = typeof data.inferred_subject === "string" ? data.inferred_subject.trim() : "";
        if (inferred && !inferredMerged) inferredMerged = inferred;
        const dsGroq = typeof data.deep_summary === "string" ? data.deep_summary.trim() : "";

        const label = data.filename || f.name;
        if (dsGroq) {
          summaryPieces.push(n === 1 ? dsGroq : `## ${String(label)}\n\n${dsGroq}`);
        }
        chunks.push(`=== ${label} ===\n\n${data.timestamped_transcript || data.transcript}`);
        mixedPieces.push({ label, view: data.transcript_mixed_view ?? null });
        const ap = data.asr_passages_annotated;
        if (Array.isArray(ap)) asrPieces.push({ label, passages: ap });
        langs.push(data.language || "");
        minutes += data.duration_minutes || 0;
      }

      const langDisplay = langs.every((l) => l && l === langs[0])
        ? langs[0] || t("common.dash")
        : t("common.mixedAuto");

      const transcriptJoined = chunks.join("\n\n---\n\n");
      const mergedMv = mergeTranscriptMixedViews(mixedPieces);
      /** Texte officiel : vue unifiée (surlignages rouge / orange) si le backend l’a renvoyée, sinon verbatim horodaté. */
      const transcriptFinal =
        mergedMv?.blocks?.length > 0 && typeof mergedMv.plain_text === "string" && mergedMv.plain_text.trim()
          ? mergedMv.plain_text
          : transcriptJoined;

      const wc = transcriptFinal.trim()
        ? transcriptFinal.trim().split(/\s+/).filter(Boolean).length
        : 0;

      setTranscript(transcriptFinal);
      setLanguage(langDisplay);
      setWordCount(wc);
      setDurationMinutes(minutes);
      setPrimaryName(files[0]?.name || "");
      setTranscriptMixedView(mergedMv);
      const mergedAsr = mergeAsrPassagesAnnotated(asrPieces);
      setAsrPassagesAnnotated(mergedAsr);

      const mergedDeep =
        summaryPieces.length === 0
          ? ""
          : summaryPieces.length === 1
            ? summaryPieces[0]
            : summaryPieces.join("\n\n---\n\n");
      const subjectFinal = (inferredMerged || subject || "").trim() || t("common.general");

      const usageSnapshot = {
        whisperAudioSeconds: wSec,
        whisperBilledMru: wMru,
        whisperApiEstimatedTokensSum: wTok,
        groqLessonInput: 0,
        groqLessonOutput: 0,
        groqLessonBilledMru: 0,
        mixedLangPromptTokens: mixPrompt,
        mixedLangCompletionTokens: mixComp,
        groqInsightPromptTokens: groqPromptTot,
        groqInsightCompletionTokens: groqCompTot,
        groqInsightOptionalBilledMru: 0,
        // Nouvelles infos pour l'affichage du modèle
        transcriptionEngine: data?.transcription_engine || data?.usage?.transcription_engine,
        retailMruPerHourApplied: data?.usage?.retail_mru_per_hour_applied,
      };
      setSessionUsage(usageSnapshot);

      const hid = newHistoryId();
      setDeepSummary(mergedDeep);
      setGroqTruncatedHint(groqTruncAny);
      setGroqInsightApplied(groqAppliedFlag);
      setInsightPanelOpen(false);
      setSubject(subjectFinal);

      prependEntry({
        id: hid,
        createdAt: new Date().toISOString(),
        displayTitle: files[0]?.name || t("history.untitled"),
        filenames: files.map((f) => f.name),
        transcript: transcriptFinal,
        transcriptMixedView: mergedMv ?? null,
        subject: subjectFinal,
        speechLanguage,
        transcriptionEngine,
        language: langDisplay,
        wordCount: wc,
        durationMinutes: minutes,
        usage: { ...usageSnapshot },
        lesson: null,
        deepSummary: mergedDeep,
        groqInsightApplied: groqAppliedFlag,
        groqTranscriptTruncated: groqTruncAny,
        asrPassagesAnnotated: mergedAsr,
        transcriptionJobPublicIds: jobPublicIdsCollected,
      });
      setCurrentHistoryId(hid);
      setCurrentJobPublicIds(jobPublicIdsCollected);
      reloadHistoryList();

      setPhase("editing");
      void reloadCreditHud();
      queueTranscriptionRatingOffer(jobPublicIdsCollected, files[0]?.name || "");
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: t("app.transcriptReady"), type: "success" },
        }),
      );
    } catch (e) {
      setTranscriptMixedView(null);
      setAsrPassagesAnnotated([]);
      const interrupted = isUploadInterruptedError(e);
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: {
            msg: interrupted ? e?.message || t("app.uploadInterrupted") : e?.message || t("app.transcribeFail"),
            type: interrupted ? "info" : "error",
          },
        }),
      );
      setPhase("upload");
    }
  };

  const runOptionalTranscriptInsight = async () => {
    if (transcript.trim().length < 80) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: t("app.insightTooShort"), type: "error" },
        }),
      );
      return;
    }
    setInsightLoading(true);
    try {
      const res = await requestTranscriptInsight(transcript, subject || "General", speechLanguage);
      const inf = typeof res.inferred_subject === "string" ? res.inferred_subject.trim() : "";
      const ds = typeof res.deep_summary === "string" ? res.deep_summary.trim() : "";
      const u = /** @type {Record<string, unknown>} */ (res.usage || {});
      const pt = Number(u.groq_insight_prompt_tokens ?? 0);
      const ct = Number(u.groq_insight_completion_tokens ?? 0);
      const insightMru = Number(u.billed_mru_groq_insight ?? 0);
      if (inf) setSubject(inf);
      setDeepSummary(ds);
      setGroqTruncatedHint(Boolean(res.groq_transcript_truncated));
      setGroqInsightApplied(true);
      setInsightPanelOpen(true);
      setSessionUsage((prev) => ({
        ...prev,
        groqInsightPromptTokens: pt,
        groqInsightCompletionTokens: ct,
        groqInsightOptionalBilledMru: Number(prev.groqInsightOptionalBilledMru || 0) + insightMru,
      }));
      if (currentHistoryId) {
        const prevU = loadHistory().find((e) => e.id === currentHistoryId)?.usage || {};
        const prevInsightMru = Number(prevU.groqInsightOptionalBilledMru ?? 0);
        updateEntry(currentHistoryId, {
          subject: inf || subject,
          deepSummary: ds,
          groqInsightApplied: true,
          groqTranscriptTruncated: Boolean(res.groq_transcript_truncated),
          usage: {
            ...prevU,
            groqInsightPromptTokens: pt,
            groqInsightCompletionTokens: ct,
            groqInsightOptionalBilledMru: prevInsightMru + insightMru,
          },
        });
        reloadHistoryList();
      }
      void reloadCreditHud();
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", { detail: { msg: t("app.insightReadyToast"), type: "success" } }),
      );
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: e?.message || t("app.insightFail"), type: "error" },
        }),
      );
    } finally {
      setInsightLoading(false);
    }
  };

  /**
   * Calcule le score moyen de fiabilité ASR (0–100) sur les passages annotés.
   * Retourne null si aucun passage annoté n'est disponible.
   */
  const computeAvgReliabilityScore = (passages) => {
    if (!Array.isArray(passages) || passages.length === 0) return null;
    const scores = passages
      .map((p) => {
        const s = p?.reliability?.score_0_100;
        return typeof s === "number" ? s : null;
      })
      .filter((s) => s !== null);
    if (scores.length === 0) return null;
    return scores.reduce((a, b) => a + b, 0) / scores.length;
  };

  const startGeneration = async () => {
    if (busy) return;
    if (!transcript.trim() || transcript.trim().length < 50) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: {
            msg: t("app.textTooShort"),
            type: "error",
          },
        }),
      );
      return;
    }

    // AMÉ 6 — Détection de qualité avant génération
    const avgScore = computeAvgReliabilityScore(asrPassagesAnnotated);
    if (avgScore !== null && avgScore < 55) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: {
            msg: t("app.lowQualityWarning", { score: Math.round(avgScore) }),
            type: "info",
          },
        }),
      );
    }

    setPhase("generating");
    void import("./components/LessonViewer.jsx").catch(() => {});
    try {
      const firstJobId = Array.isArray(currentJobPublicIds) && currentJobPublicIds.length > 0 ? currentJobPublicIds[0] : null;

      const result = await generateLesson(
        transcript,
        subject || "General",
        transcriptMixedView,
        asrPassagesAnnotated,
        i18n.language,
        firstJobId,
      );
      const gu = result.usage || {};
      const cin = gu.groq_input_tokens ?? result.input_tokens ?? 0;
      const cout = gu.groq_output_tokens ?? result.output_tokens ?? 0;
      const cmru = Number(gu.billed_mru_groq ?? 0);

      const lessonStr = normalizeLessonMarkdown(result?.lesson);
      setLesson(lessonStr);

      setSessionUsage((prev) => ({
        ...prev,
        groqLessonInput: cin,
        groqLessonOutput: cout,
        groqLessonBilledMru: cmru,
      }));

      if (currentHistoryId) {
        updateEntry(currentHistoryId, {
          transcript,
          transcriptMixedView,
          subject,
          language,
          wordCount,
          lesson: lessonStr,
          durationMinutes,
          asrPassagesAnnotated,
          usage: {
            ...(loadHistory().find((e) => e.id === currentHistoryId)?.usage || {}),
            groqLessonInput: cin,
            groqLessonOutput: cout,
            groqLessonBilledMru: cmru,
          },
        });
        reloadHistoryList();
      }

      setLessonViewBoundaryKey((k) => k + 1);
      setActiveTab("lesson");
      setPhase("lesson");
      void reloadCreditHud();
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: t("app.courseReadyToast"), type: "success" },
        }),
      );
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: {
            msg: e?.message || t("app.genFail"),
            type: "error",
          },
        }),
      );
      setPhase("editing");
    }
  };

  const exportTxt = () => {
    const blob = new Blob([transcript], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${exportBaseName || "transcript"}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    window.dispatchEvent(
      new CustomEvent("lecturai-toast", {
        detail: { msg: t("app.exportTxtOk"), type: "success" },
      }),
    );
  };

  // Route publique `/c/<token>` — court-circuite tout le pipeline auth / wallet / hooks lourds.
  // Le visiteur peut ne pas être connecté ; on render directement la leçon partagée.
  if (publicShareToken) {
    return (
      <div className="relative min-h-[100dvh] bg-gradient-to-b from-orange-50/70 via-white to-amber-50/40 text-slate-900 transition dark:from-slate-950 dark:via-slate-950 dark:to-orange-950/20 dark:text-slate-50">
        <ToastPortal />
        <PublicLessonView token={publicShareToken} />
      </div>
    );
  }

  if (authGate.loading) {
    return (
      <div className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden bg-gradient-to-b from-orange-50/90 via-amber-50/35 to-white dark:from-slate-950 dark:via-orange-950/15 dark:to-slate-950">
        <div className="pointer-events-none absolute inset-0 bg-dot-grid opacity-50 motion-reduce:hidden dark:opacity-30" aria-hidden />
        <div className="glass-panel relative z-10 flex flex-col items-center gap-4 rounded-3xl px-10 py-12 shadow-soft-lg motion-safe:animate-fade-in-up">
          <div className="size-11 animate-spin rounded-full border-4 border-brand-600/25 border-t-brand-600 dark:border-brand-400/20 dark:border-t-brand-400" />
          <p className="text-sm font-medium text-slate-600 dark:text-slate-400">{t("app.checkingAccess")}</p>
        </div>
      </div>
    );
  }

  if (authGate.loadError) {
    return (
      <div className="relative flex min-h-[100dvh] flex-col items-center justify-center gap-4 overflow-hidden bg-gradient-to-b from-slate-50 via-rose-50/30 to-white px-4 text-center dark:from-slate-950 dark:via-rose-950/10 dark:to-slate-950">
        <div className="pointer-events-none absolute inset-0 bg-dot-grid opacity-40 dark:opacity-25" aria-hidden />
        <p className="relative z-10 max-w-md text-sm text-slate-700 dark:text-slate-300">
          <Trans
            i18nKey="app.apiError"
            components={{
              cmd: <code className="rounded bg-slate-200 px-1 py-0.5 text-xs dark:bg-slate-800" />,
            }}
          />
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="relative z-10 rounded-2xl bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white shadow-glow hover:brightness-105"
        >
          {t("common.reload")}
        </button>
      </div>
    );
  }

  if (!authGate.skipped && !authGate.ready) {
    return (
      <div className="relative flex min-h-[100dvh] min-w-0 flex-col overflow-x-hidden bg-gradient-to-b from-orange-50/70 via-white to-amber-50/40 text-slate-900 transition dark:from-slate-950 dark:via-slate-950 dark:to-orange-950/20 dark:text-slate-50">
        <div className="pointer-events-none fixed inset-0 bg-dot-grid opacity-40 dark:opacity-[0.2]" aria-hidden />
        <div className="pointer-events-none fixed inset-0 hidden overflow-hidden lg:block" aria-hidden>
          <div className="absolute -left-[18%] top-[-12%] h-[min(400px,70vw)] w-[min(400px,95vw)] rounded-full bg-brand-400/28 blur-[100px] motion-safe:animate-blob-drift dark:bg-brand-600/14" />
          <div className="absolute bottom-[-18%] right-[-12%] h-[min(380px,65vw)] w-[min(380px,92vw)] rounded-full bg-rose-300/22 blur-[110px] motion-safe:animate-blob-drift-reverse dark:bg-rose-600/12" />
        </div>
        <ToastPortal />
        <AuthScreen onAuthed={handleAuthed} />
      </div>
    );
  }

  if (authGate.ready && !authGate.skipped && profileFlags.is_admin) {
    const adminEmail = getAuthProfile()?.email;
    return (
      <div className="relative flex min-h-[100dvh] w-full min-w-0 max-w-[100%] flex-col overflow-x-hidden bg-gradient-to-b from-orange-50/80 via-white to-amber-50/30 text-slate-900 transition dark:from-slate-950 dark:via-orange-950/[0.12] dark:to-slate-950 dark:text-slate-50">
        <div className="pointer-events-none fixed inset-0 bg-dot-grid opacity-35 dark:opacity-[0.18]" aria-hidden />
        {/* Même logique que l’app principale : pas de blobs animés < lg (Safari mobile + débordements visuels). */}
        <div className="pointer-events-none fixed inset-0 hidden overflow-hidden lg:block" aria-hidden>
          <div className="absolute -left-[15%] top-[-14%] h-[min(400px,70vw)] w-[min(400px,92vw)] rounded-full bg-brand-400/22 blur-[100px] motion-safe:animate-blob-drift dark:bg-brand-600/12" />
          <div className="absolute bottom-[-14%] right-[-12%] h-[min(400px,60vw)] w-[min(400px,85vw)] rounded-full bg-amber-400/18 blur-[110px] motion-safe:animate-blob-drift-reverse dark:bg-amber-700/10" />
        </div>

        <ToastPortal />
        <header className="sticky top-0 z-50 w-full min-w-0 overflow-x-hidden px-3 pt-3 pb-1 sm:px-6">
          <div className="glass-header mx-auto w-full min-w-0 max-w-full overflow-x-clip rounded-2xl px-4 py-3 sm:max-w-5xl sm:rounded-3xl sm:px-6">
            <div className="flex w-full min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between lg:items-center">
              <div className="min-w-0 w-full flex-1 sm:w-auto">
                <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1 font-display text-xl font-extrabold tracking-tight">
                  <span className="text-gradient-brand">LecturAI</span>
                  <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-slate-400">
                    {t("app.adminBadge")}
                  </span>
                </span>
                <p className="mt-1 max-w-full text-[11px] leading-snug text-slate-500 [overflow-wrap:anywhere] dark:text-slate-400 sm:max-w-xl">
                  {t("app.adminSubtitle")}
                </p>
              </div>
              <div className="flex w-full min-w-0 flex-col items-stretch gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
                <WhatsAppSupportButton variant="admin" className="w-full justify-center sm:w-auto" />
                <LanguageSwitcher className="w-full justify-center sm:w-auto sm:justify-self-auto" />
                <button
                  type="button"
                  onClick={() => {
                    clearAuthSession();
                    setProfileFlags({ is_admin: false });
                    setAuthGate({ loading: false, skipped: false, ready: false, loadError: false });
                  }}
                  className="w-full rounded-full border border-rose-200/80 bg-rose-500/10 px-3 py-2 text-[11px] font-semibold text-rose-800 shadow-sm transition hover:bg-rose-500/15 dark:border-rose-900/60 dark:text-rose-200 dark:hover:bg-rose-950/50 sm:w-auto sm:px-3.5"
                >
                  {t("app.logout")}
                </button>
                <button
                  type="button"
                  onClick={() => setDark((d) => !d)}
                  className="w-full rounded-full border border-slate-200/90 bg-white/60 px-3 py-2 text-[11px] font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-100 dark:hover:bg-slate-800 sm:w-auto sm:px-3.5"
                  aria-label={t("common.toggleTheme")}
                >
                  {dark ? `☀️ ${t("common.light")}` : `🌙 ${t("common.dark")}`}
                </button>
                {adminEmail ? (
                  <span
                    title={adminEmail}
                    className="hidden max-w-[min(100%,14rem)] truncate text-[10px] font-medium text-slate-500 sm:inline dark:text-slate-400"
                  >
                    {adminEmail}
                  </span>
                ) : null}
              </div>
            </div>
          </div>
        </header>

        <main className="admin-main-pad relative z-10 mx-auto w-full min-w-0 max-w-5xl flex-1 py-8 sm:py-12 lg:py-14">
          <div className="min-w-0 max-w-full overflow-x-clip">
            <Suspense fallback={<RouteLazyFallback />}>
              <AdminTopUpsPage />
            </Suspense>
          </div>
        </main>

        <footer className="admin-main-pad relative z-10 mx-auto w-full min-w-0 max-w-5xl pb-8 text-center text-[11px] text-slate-500 dark:text-slate-500">
          {t("app.adminFooter")}
        </footer>
      </div>
    );
  }

  const profileEmail = getAuthProfile()?.email;

  return (
    <div className="relative flex min-h-[100dvh] min-w-0 flex-col overflow-x-hidden bg-gradient-to-b from-orange-50/75 via-white to-rose-50/25 text-slate-900 transition dark:from-slate-950 dark:via-slate-950 dark:to-orange-950/25 dark:text-slate-50">
      <div className="pointer-events-none fixed inset-0 bg-dot-grid opacity-[0.45] dark:opacity-[0.22]" aria-hidden />
      {/* Blobs animés : masqués < lg pour alléger le GPU mobile (zones du bas qui “arrivent” en retard). */}
      <div className="pointer-events-none fixed inset-0 hidden overflow-hidden lg:block" aria-hidden>
        <div className="absolute -left-[20%] top-[-14%] h-[min(460px,60vw)] w-[min(460px,85vw)] rounded-full bg-brand-400/30 blur-[100px] motion-safe:animate-blob-drift dark:bg-brand-600/16" />
        <div className="absolute bottom-[-18%] right-[-14%] h-[min(440px,55vw)] w-[min(440px,82vw)] rounded-full bg-amber-300/25 blur-[110px] motion-safe:animate-blob-drift-reverse dark:bg-amber-600/12" />
        <div className="absolute left-1/2 top-[38%] h-72 w-72 -translate-x-1/2 rounded-full bg-rose-300/20 blur-[100px] motion-safe:animate-breathe dark:bg-rose-600/10" />
      </div>

      <ToastPortal />
      <TranscriptionRatingModal
        open={transcriptionRatingModalOpen}
        jobPublicIds={transcriptionRatingOffer?.jobPublicIds ?? []}
        fileLabel={transcriptionRatingOffer?.label}
        onClose={() => {
          setTranscriptionRatingModalOpen(false);
          setTranscriptionRatingOffer((prev) =>
            prev && wasRatingPromptHandled(prev.jobPublicIds) ? null : prev,
          );
        }}
      />
      <IdeaFeedbackModal open={ideaFeedbackOpen} uiLocale={i18n.language} onClose={() => setIdeaFeedbackOpen(false)} />
      <header className="sticky top-0 z-50 px-2 pt-[max(0.5rem,env(safe-area-inset-top,0px))] pb-1 sm:px-4 lg:px-8">
        <div className="glass-header mx-auto min-w-0 max-w-full rounded-2xl border border-brand-200/20 px-3 py-2.5 shadow-sm shadow-brand-900/[0.03] sm:max-w-6xl sm:rounded-3xl sm:px-6 sm:py-3.5 dark:border-white/5 dark:shadow-black/20">
          <div className="flex flex-col gap-2.5 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between sm:gap-3 lg:items-center">
            <div className="flex min-w-0 flex-1 items-start gap-3">
              <div className="min-w-0">
                <span className="flex flex-wrap items-baseline gap-2 font-display">
                  <span className="text-fluid-hero leading-none font-extrabold tracking-tight text-slate-900 dark:text-white">
                    <span className="text-gradient-brand">LecturAI</span>
                  </span>
                  <span className="inline-flex shrink-0 items-center rounded-full border border-brand-500/25 bg-brand-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.22em] text-brand-700 dark:border-brand-400/35 dark:bg-brand-500/15 dark:text-brand-200">
                    {t("app.beta")}
                  </span>
                  <span
                    className="inline-flex shrink-0 items-center rounded-full border border-slate-200/90 bg-white/85 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-600 shadow-sm dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-300 sm:hidden"
                    aria-live="polite"
                  >
                    {badges.short}
                  </span>
                </span>
                <p className="mt-2 max-w-md text-[12px] leading-relaxed text-slate-600 dark:text-slate-400 sm:text-[13px]">
                  {t("meta.descriptionShort")}
                </p>
              </div>
            </div>

            <div className="scroll-x-contained -mx-1 flex w-full min-w-0 flex-nowrap items-center gap-2 overflow-x-auto px-1 pb-0.5 sm:mx-0 sm:w-auto sm:flex-wrap sm:overflow-visible sm:px-0 sm:pb-0">
              <span className="hidden shrink-0 items-center rounded-full border border-transparent bg-slate-900/[0.04] px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500 dark:bg-white/[0.04] dark:text-slate-400 sm:inline-flex">
                {badges.full}
              </span>
              {!authGate.skipped ? (
                <button
                  type="button"
                  disabled={busy}
                  title={
                    creditHud?.blocked && !creditHud.canUse ? creditHud.blocked : t("app.walletTitle")
                  }
                  onClick={() => setPhase("credits")}
                  className={`shrink-0 rounded-full border px-3 py-2.5 text-[11px] font-bold shadow-sm backdrop-blur-sm transition hover:brightness-[1.02] disabled:cursor-not-allowed disabled:opacity-45 tabular-nums sm:min-h-0 sm:px-4 sm:py-2 ${
                    creditHud && !creditHud.canUse
                      ? "border-amber-300/70 bg-gradient-to-br from-amber-100 to-amber-50/70 text-amber-950 shadow-amber-200/30 dark:border-amber-900/60 dark:from-amber-950/60 dark:to-amber-950/30 dark:text-amber-100"
                      : "border-brand-300/50 bg-gradient-to-br from-white to-brand-50/90 text-brand-900 shadow-brand-500/10 dark:border-brand-800/50 dark:from-brand-950/40 dark:to-slate-900/90 dark:text-brand-100"
                  }`}
                >
                  <span className="opacity-85">MRU</span>
                  <span>{creditHud != null ? ` · ${creditHud.balance}` : ""}</span>
                </button>
              ) : null}
              {!authGate.skipped ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setPhase("notifications");
                    void reloadNotifUnread();
                  }}
                  title={t("app.notificationsTitle")}
                  aria-label={
                    notifUnread > 0
                      ? t("app.notificationsAriaUnread", { count: notifUnread })
                      : t("app.notificationsAriaEmpty")
                  }
                  className="relative inline-flex shrink-0 items-center gap-1 rounded-full border border-slate-200/90 bg-white/75 px-3 py-2.5 text-[11px] font-bold text-slate-800 shadow-sm backdrop-blur-md transition hover:border-slate-300 hover:bg-white disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800 sm:py-2 sm:px-3"
                >
                  <span aria-hidden className="text-base leading-none">🔔</span>
                  {notifUnread > 0 ? (
                    <span className="absolute -end-1 -top-1 inline-flex min-h-[1.1rem] min-w-[1.1rem] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold leading-none text-white shadow-sm ring-2 ring-white motion-safe:animate-pulse dark:ring-slate-900">
                      {notifUnread > 99 ? "99+" : notifUnread}
                    </span>
                  ) : null}
                </button>
              ) : null}
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  reloadHistoryList();
                  setPhase("history");
                }}
                className="shrink-0 rounded-full border border-slate-200/90 bg-white/75 px-3 py-2.5 text-[11px] font-bold text-slate-800 shadow-sm backdrop-blur-md transition hover:border-slate-300 hover:bg-white disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800 sm:py-2 sm:px-4"
              >
                {t("app.historyBtn")}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setPhase("chat")}
                className="shrink-0 rounded-full border border-slate-200/90 bg-white/75 px-3 py-2.5 text-[11px] font-bold text-slate-800 shadow-sm backdrop-blur-md transition hover:border-slate-300 hover:bg-white disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800 sm:py-2 sm:px-4"
              >
                {t("app.chatBtn")}
              </button>
              <LanguageSwitcher className="rounded-full border border-slate-200/90 bg-white/75 px-1.5 py-0.5 text-[11px] font-bold text-slate-800 shadow-sm backdrop-blur-md dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-100 sm:px-2 sm:py-1.5" />
              <button
                type="button"
                disabled={busy}
                onClick={() => setIdeaFeedbackOpen(true)}
                className="shrink-0 rounded-full border border-emerald-200/80 bg-emerald-50/90 px-3 py-2.5 text-[11px] font-bold text-emerald-900 shadow-sm backdrop-blur-md transition hover:bg-emerald-100/90 disabled:cursor-not-allowed disabled:opacity-45 dark:border-emerald-900/55 dark:bg-emerald-950/50 dark:text-emerald-100 dark:hover:bg-emerald-950/80 sm:py-2 sm:px-4"
              >
                {t("feedback.ideasBtn")}
              </button>
              <WhatsAppSupportButton variant="header" />
              {authGate.skipped !== true && profileEmail ? (
                <span
                  title={profileEmail}
                  className="order-last hidden max-w-[10rem] truncate text-[10px] font-medium text-slate-500 sm:inline dark:text-slate-400 md:max-w-[14rem] md:text-[11px]"
                >
                  {profileEmail}
                </span>
              ) : null}
              {!authGate.skipped && authGate.ready ? (
                <button
                  type="button"
                  onClick={() => {
                    clearAuthSession();
                    setPhase("upload");
                    setAuthGate({ loading: false, skipped: false, ready: false, loadError: false });
                  }}
                  className="shrink-0 rounded-full border border-rose-300/60 bg-white/85 px-3 py-2.5 text-[11px] font-bold text-rose-800 shadow-sm transition hover:bg-rose-50 dark:border-rose-900/55 dark:bg-rose-950/40 dark:text-rose-200 dark:hover:bg-rose-950/70 sm:py-2 sm:px-4"
                >
                  {t("app.logout")}
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => setDark((d) => !d)}
                className="inline-flex min-h-[2.75rem] shrink-0 items-center rounded-full border border-slate-200/90 bg-slate-900/[0.03] px-3 py-2 text-[11px] font-semibold text-slate-700 transition hover:bg-white dark:border-slate-600 dark:bg-white/[0.05] dark:text-slate-200 dark:hover:bg-slate-800 sm:min-h-0 sm:py-2"
                aria-label={t("common.toggleTheme")}
              >
                <span aria-hidden>{dark ? "☀️" : "🌙"}</span>
                <span className="ms-1 hidden sm:inline">{dark ? t("common.light") : t("common.dark")}</span>
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto w-full min-w-0 max-w-6xl flex-1 safe-pad-x py-6 sm:px-6 sm:py-8 lg:px-10 lg:py-12 pb-[max(3.25rem,env(safe-area-inset-bottom,0px)+1.75rem)] sm:pb-14 xl:pb-20">
        {phase === "history" && (
          <Suspense fallback={<RouteLazyFallback />}>
            <TranscriptionHistoryPage
              items={historyItems}
              onBack={() => setPhase("upload")}
              onOpen={openHistoryEntry}
              onDelete={deleteHistoryEntry}
            />
          </Suspense>
        )}

        {phase === "credits" && (
          <Suspense fallback={<RouteLazyFallback />}>
            <CreditsPage onBack={() => setPhase("upload")} onWalletUpdated={reloadCreditHud} />
          </Suspense>
        )}

        {phase === "notifications" && (
          <Suspense fallback={<RouteLazyFallback />}>
            <NotificationsPage
              onBack={() => setPhase("upload")}
              onUnreadChange={(n) => setNotifUnread(Math.max(0, Number(n) || 0))}
            />
          </Suspense>
        )}

        {phase === "chat" && (
          <Suspense fallback={<RouteLazyFallback />}>
            <ChatPage onBack={() => setPhase("upload")} onWalletUpdated={reloadCreditHud} />
          </Suspense>
        )}

        {phase === "upload" && (
          <>
            <UploadZone
              files={files}
              onFilesChange={setFiles}
              subject={subject}
              onSubjectChange={setSubject}
              speechLanguage={speechLanguage}
              onSpeechLanguageChange={setSpeechLanguage}
              transcriptionEngine={transcriptionEngine}
              onTranscriptionEngineChange={setTranscriptionEngine}
              disabled={busy}
              batchProgress={batchProgress}
              onSubmit={startTranscription}
            />
            <BgTranscribeJobsPanel
              authReady={authGate.ready}
              onOpenJob={openBgJobFromList}
              onWalletUpdated={reloadCreditHud}
            />
          </>
        )}

        {phase === "transcribing" && (
          <>
            {files.length === 0 ? (
              <div className="mx-auto mb-6 w-full max-w-xl space-y-2 text-center">
                <button
                  type="button"
                  onClick={() => goUpload()}
                  className="rounded-2xl border border-slate-200/90 bg-white/85 px-4 py-2.5 text-sm font-semibold text-slate-800 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/85 dark:text-slate-100 dark:hover:bg-slate-800"
                >
                  {t("bgJobs.resumeBack")}
                </button>
                <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{t("bgJobs.serverPersistHint")}</p>
              </div>
            ) : null}
            {files.length > 0 && (
              <section className="glass-panel mx-auto mb-8 w-full max-w-xl space-y-3 rounded-3xl p-5 shadow-soft">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-400">
                  {t("transcribe.sectionTitle")}
                </div>
                <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
                  {t("transcribe.barExplain", {
                    upload: Math.round(TRANSCRIBE_UPLOAD_WEIGHT * 100),
                    server: Math.round(TRANSCRIBE_SERVER_WEIGHT * 100),
                  })}
                </p>
                <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-brand-600 to-brand-400 transition-[width]"
                    style={{ width: `${Math.round((batchProgress.overallPct || 0) * 100)}%` }}
                  />
                </div>
                <ul className="space-y-2">
                  {files.map((f, i) => {
                    const p = batchProgress.perFile[i] ?? 0;
                    return (
                      <li key={`${f.name}-${f.size}`}>
                        <div className="flex items-center justify-between gap-3 text-xs text-slate-600 dark:text-slate-300">
                          <span className="truncate">{f.name}</span>
                          <span className="shrink-0 font-semibold text-brand-600 dark:text-brand-400">
                            {t("transcribe.global")} {Math.round(p * 100)}%
                          </span>
                        </div>
                        <div className="mt-1 h-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                          <div
                            className="h-full rounded-full bg-brand-600 transition-[width] dark:bg-brand-400"
                            style={{ width: `${Math.round(p * 100)}%` }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}
            <TranscriptionProgressPanel transcriptionName={ENGINE_TRANSCRIPTION} live={transcribeLive ?? {}} />
          </>
        )}

        {phase === "editing" && (
          <div className="mx-auto max-w-3xl space-y-8">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="space-y-1">
                <h2 className="font-display text-2xl font-bold text-slate-900 dark:text-white">
                  {t("app.polishTitle")}
                </h2>
                <p className="max-w-xl text-sm leading-relaxed text-slate-600 dark:text-slate-400">
                  {t("app.polishBody", { transcription: ENGINE_TRANSCRIPTION, course: ENGINE_COURSE })}
                </p>
              </div>
              <button
                type="button"
                onClick={goUpload}
                className="rounded-2xl border border-slate-200/90 bg-white/70 px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
              >
                {t("app.newImport")}
              </button>
            </div>

            {transcript.trim().length >= 80 && (
              <div className="rounded-2xl border border-slate-200/80 bg-slate-50/80 px-4 py-3 text-sm text-slate-700 shadow-sm dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-200">
                <p className="font-medium text-slate-900 dark:text-slate-50">{t("app.insightOptionalTitle", { insight: ENGINE_INSIGHT })}</p>
                <p className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{t("app.insightOptionalHint")}</p>
                {!insightPanelOpen ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {String(deepSummary || "").trim() ? (
                      <button
                        type="button"
                        onClick={() => setInsightPanelOpen(true)}
                        className="rounded-xl border border-brand-200 bg-white px-4 py-2 text-xs font-semibold text-brand-800 transition hover:bg-brand-50 dark:border-brand-800 dark:bg-slate-900 dark:text-brand-200 dark:hover:bg-slate-800"
                      >
                        {t("app.insightShowBtn")}
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={insightLoading || busy}
                        onClick={() => void runOptionalTranscriptInsight()}
                        className="rounded-xl bg-gradient-to-r from-brand-600 to-amber-600 px-4 py-2 text-xs font-semibold text-white shadow-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {insightLoading ? t("app.insightLoading") : t("app.insightRunBtn")}
                      </button>
                    )}
                  </div>
                ) : null}
              </div>
            )}

            <InsightPanel
              subject={subject}
              onSubjectChange={setSubject}
              deepSummary={deepSummary}
              truncated={groqTruncatedHint}
              usage={sessionUsage}
              show={insightPanelOpen}
              onClose={() => setInsightPanelOpen(false)}
            />

            <TranscriptEditor
              value={transcript}
              onChange={setTranscript}
              language={language}
              speechLanguageChosen={speechLanguage === "ar" ? t("langs.arabic") : t("langs.french")}
              wordCount={wordCount}
              durationMinutes={durationMinutes}
              primaryFileName={primaryName}
              onExportTxt={exportTxt}
              onGenerateCourse={startGeneration}
              busy={busy}
              usage={sessionUsage}
              mixedView={transcriptMixedView}
            />

            {showTranscriptionRatingCTA ? (
              <div className="flex justify-center">
                <button
                  type="button"
                  onClick={() => setTranscriptionRatingModalOpen(true)}
                  className="rounded-2xl border border-amber-200/90 bg-amber-50/90 px-5 py-2.5 text-sm font-semibold text-amber-950 shadow-sm transition hover:bg-amber-100 dark:border-amber-800/60 dark:bg-amber-950/40 dark:text-amber-100 dark:hover:bg-amber-950/60"
                >
                  {t("feedback.ratingOpenBtn")}
                </button>
              </div>
            ) : null}

            <p className="text-xs leading-relaxed text-slate-500 dark:text-slate-500">
              {t("app.mruFootnote")}
            </p>

            <div className="glass-panel rounded-3xl border border-brand-200/60 bg-gradient-to-br from-brand-50/90 via-white to-amber-50/45 p-8 text-center shadow-soft dark:border-brand-900/40 dark:from-slate-900/80 dark:via-slate-900 dark:to-brand-950/15">
              <h3 className="font-display text-xl font-bold text-slate-900 dark:text-white">
                {t("app.structuredTitle")}
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-600 dark:text-slate-400">
                {t("app.structuredBody", { course: ENGINE_COURSE })}
              </p>
              <button
                type="button"
                onClick={startGeneration}
                disabled={busy}
                className="mt-6 inline-flex items-center gap-2 rounded-2xl bg-gradient-to-r from-brand-600 via-amber-500 to-rose-500 px-8 py-3.5 text-sm font-bold text-white shadow-glow transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none dark:disabled:opacity-40"
              >
                {t("app.genCourse")}
              </button>
            </div>
          </div>
        )}

        {phase === "generating" && <GeneratingSkeleton />}

        {phase === "lesson" && (
          <>
            <RouteErrorBoundary
              key={lessonViewBoundaryKey}
              fallback={(error) => (
                <div className="glass-panel mx-auto max-w-3xl space-y-4 rounded-3xl border border-rose-200/80 bg-rose-50/50 p-8 text-center shadow-soft dark:border-rose-900/50 dark:bg-rose-950/25">
                  <h2 className="font-display text-xl font-bold text-rose-950 dark:text-rose-100">
                    {t("lesson.renderErrorTitle")}
                  </h2>
                  <p className="text-sm leading-relaxed text-rose-900/90 dark:text-rose-200/90">
                    {t("lesson.renderErrorBody")}
                  </p>
                  <pre className="mx-auto max-w-full overflow-auto rounded-xl bg-white/50 p-3 text-[10px] text-rose-800 dark:bg-black/20 dark:text-rose-300">
                    {error?.message || String(error)}
                  </pre>
                  <button
                    type="button"
                    onClick={() => {
                      setLesson("");
                      setPhase("editing");
                    }}
                    className="rounded-2xl bg-brand-600 px-6 py-3 text-sm font-semibold text-white shadow-glow transition hover:brightness-105"
                  >
                    {t("app.backTranscript")}
                  </button>
                </div>
              )}
            >
              <Suspense fallback={<RouteLazyFallback />}>
                <LessonViewer
                  activeTab={activeTab}
                  onTabChange={setActiveTab}
                  transcript={transcript}
                  transcriptMixedView={transcriptMixedView}
                  lesson={lesson}
                  subject={subject || "General"}
                  filename={exportBaseName}
                  language={language}
                  speechChosenLabel={speechLanguage === "ar" ? t("langs.arabic") : t("langs.french")}
                  wordCount={wordCount}
                  durationMinutes={durationMinutes}
                  celebration={celebrate}
                  usage={sessionUsage}
                  jobPublicId={Array.isArray(currentJobPublicIds) && currentJobPublicIds.length === 1 ? currentJobPublicIds[0] : null}
                />
              </Suspense>
            </RouteErrorBoundary>
            {showTranscriptionRatingCTA ? (
              <div className="mx-auto mt-6 flex max-w-3xl justify-center">
                <button
                  type="button"
                  onClick={() => setTranscriptionRatingModalOpen(true)}
                  className="rounded-2xl border border-amber-200/90 bg-amber-50/90 px-5 py-2.5 text-sm font-semibold text-amber-950 shadow-sm transition hover:bg-amber-100 dark:border-amber-800/60 dark:bg-amber-950/40 dark:text-amber-100 dark:hover:bg-amber-950/60"
                >
                  {t("feedback.ratingOpenBtn")}
                </button>
              </div>
            ) : null}
            <div className="mx-auto mt-10 flex max-w-3xl flex-wrap justify-between gap-3">
              <button
                type="button"
                onClick={() => {
                  setLesson("");
                  setPhase("editing");
                }}
                className="rounded-2xl border border-slate-200/90 bg-white/60 px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                {t("app.backTranscript")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setLesson("");
                  setTranscript("");
                  setTranscriptMixedView(null);
                  setAsrPassagesAnnotated([]);
                  setDeepSummary("");
                  setGroqTruncatedHint(false);
                  setGroqInsightApplied(false);
                  setInsightPanelOpen(false);
                  setInsightLoading(false);
                  setFiles([]);
                  setSpeechLanguage("fr");
                  setPhase("upload");
                  setBatchProgress({ perFile: [], overallPct: 0 });
                  setSessionUsage({ ...INITIAL_SESSION_USAGE });
                  setCurrentHistoryId(null);
                }}
                className="rounded-2xl border border-transparent px-4 py-2.5 text-sm font-semibold text-brand-700 transition hover:bg-brand-500/10 dark:text-brand-300 dark:hover:bg-brand-950"
              >
                {t("app.newSession")}
              </button>
            </div>
          </>
        )}
      </main>

      <footer className="relative z-10 mx-4 mb-6 mt-auto rounded-3xl border border-brand-200/30 bg-gradient-to-br from-white/80 to-orange-50/40 px-4 py-6 text-center text-xs leading-relaxed text-slate-600 shadow-sm backdrop-blur-md dark:border-brand-900/30 dark:from-slate-950/80 dark:to-brand-950/20 dark:text-slate-400 sm:mx-auto sm:max-w-6xl sm:px-6 safe-pad-b">
        {t("app.footer")}
      </footer>
    </div>
  );
}
