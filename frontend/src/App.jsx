import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import AuthScreen from "./components/AuthScreen.jsx";
import AdminTopUpsPage from "./components/AdminTopUpsPage.jsx";
import BgTranscribeJobsPanel from "./components/BgTranscribeJobsPanel.jsx";
import CreditsPage from "./components/CreditsPage.jsx";
import LessonViewer from "./components/LessonViewer.jsx";
import TranscriptEditor from "./components/TranscriptEditor.jsx";
import TranscriptionHistoryPage from "./components/TranscriptionHistoryPage.jsx";
import UploadZone from "./components/UploadZone.jsx";
import WhatsAppSupportButton from "./components/WhatsAppSupportButton.jsx";
import { ENGINE_COURSE, ENGINE_TRANSCRIPTION } from "./branding.js";
import {
  TRANSCRIBE_SERVER_WEIGHT,
  TRANSCRIBE_UPLOAD_WEIGHT,
  TranscriptionJobFailedError,
  apiUrl,
  enqueueTranscribeJobWithXHR,
  isUploadInterruptedError,
  generateLesson,
  getAuthHeaders,
  parseJsonResponse,
  waitForTerminalTranscriptionJob,
} from "./utils/api.js";
import { loadBgTranscribeJobIds, forgetBgTranscribeJobId, persistBgTranscribeJobId } from "./utils/bgTranscribeJobsStorage.js";
import { clearAuthSession, getAuthProfile, getAuthToken, setAuthSession } from "./utils/authStorage.js";
import { loadHistory, prependEntry, removeEntry, updateEntry } from "./utils/transcriptionHistory.js";
import { mergeTranscriptMixedViews } from "./utils/transcriptMixedView.js";

const INITIAL_SESSION_USAGE = {
  whisperAudioSeconds: 0,
  whisperBilledMru: 0,
  whisperApiEstimatedTokensSum: 0,
  claudeInput: 0,
  claudeOutput: 0,
  claudeBilledMru: 0,
  mixedLangPromptTokens: 0,
  mixedLangCompletionTokens: 0,
};

function phaseBadgeLabels(ph) {
  switch (ph) {
    case "upload":
      return { short: "Import", full: "Importer" };
    case "transcribing":
      return { short: "Audio", full: "Transcription" };
    case "editing":
      return { short: "Texte", full: "Révision" };
    case "generating":
      return { short: "IA…", full: "Cours" };
    case "lesson":
      return { short: "Prêt", full: "Prêt" };
    case "history":
      return { short: "Liste", full: "Historique" };
    case "credits":
      return { short: "MRU", full: "Portefeuille" };
    default:
      return { short: "Étape", full: ph };
  }
}

function newHistoryId() {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  } catch {
    /* noop */
  }
  return `h-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

/** @param {Record<string, unknown>} data Réponse brute API transcription (succès). */
function historyEntryFromTranscribePayload(data, { filenames, subject, speechLanguage, historyId }) {
  const label =
    typeof data.filename === "string" && data.filename.trim()
      ? data.filename.trim()
      : filenames[0] || "Sans titre";
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
  const usageSnapshot = {
    whisperAudioSeconds: Number(su.whisper_duration_seconds ?? 0),
    whisperBilledMru: Number(su.billed_mru_transcription_total ?? su.billed_mru_whisper ?? 0),
    whisperApiEstimatedTokensSum: Number(su.transcript_estimated_tokens ?? 0),
    mixedLangPromptTokens: Number(su.segment_translation_prompt_tokens ?? 0),
    mixedLangCompletionTokens: Number(su.segment_translation_completion_tokens ?? 0),
    claudeInput: 0,
    claudeOutput: 0,
    claudeBilledMru: 0,
  };
  return {
    id: historyId,
    createdAt: new Date().toISOString(),
    displayTitle: label,
    filenames,
    transcript: transcriptFinal,
    transcriptMixedView: mergedMv ?? null,
    subject,
    speechLanguage,
    language: String(data.language || "—"),
    wordCount: wc,
    durationMinutes: Number(data.duration_minutes ?? 0),
    usage: usageSnapshot,
    lesson: null,
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
      className={`fixed bottom-6 right-4 z-[200] flex max-w-sm items-start gap-3 rounded-2xl border px-4 py-3 text-sm shadow-soft-lg glass-panel motion-safe:animate-toast-in dark:!bg-slate-900/90 ${accent} safe-pad-x`}
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
    accent === "violet"
      ? "text-violet-600 dark:text-violet-400"
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
        kicker: "Étape 1",
        title: "Réception du média",
        body: "Ton fichier est mis en sécurité sur le serveur, vérifié puis préparé pour l’analyse.",
      },
      {
        icon: "🎙️",
        kicker: "Étape 2",
        title: `${transcriptionName} aligne l’audio et le texte`,
        body: "Le moteur Whisper convertit chaque seconde audio en mots, en détectant automatiquement la langue parlée.",
      },
      {
        icon: "⏱️",
        kicker: "Étape 3",
        title: "Repères temporels toutes les 30 s",
        body: "Des marques de temps sont insérées pour pouvoir naviguer rapidement dans la transcription finale.",
      },
      {
        icon: "💡",
        kicker: "Astuce",
        title: "Tu peux fermer l’onglet sans risque",
        body: "Le travail continue côté serveur — retrouve l’avancement dans « Fichiers récents » sur l’accueil.",
      },
    ],
    [transcriptionName],
  );

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
    const t = setInterval(() => {
      setSlide((cur) => (cur + 1) % slides.length);
      setSlideDir(1);
    }, 4200);
    return () => clearInterval(t);
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
    (typeof live.message === "string" && live.message.trim()) || "Transcription en cours…";
  const current = slides[slide];

  return (
    <div className="mx-auto w-full max-w-xl space-y-6">
      <div className="glass-panel relative overflow-hidden rounded-3xl p-8 text-center shadow-soft">
        <div className="pointer-events-none absolute -top-24 left-1/2 size-72 -translate-x-1/2 rounded-full bg-brand-400/20 blur-3xl motion-safe:animate-blob-drift dark:bg-brand-600/15" />
        <div className="pointer-events-none absolute -bottom-24 -right-10 size-60 rounded-full bg-violet-400/15 blur-3xl motion-safe:animate-blob-drift-reverse dark:bg-violet-700/15" />

        <div className="relative mx-auto mb-6 grid max-w-sm grid-cols-3 gap-2">
          <StatPill label="Envoi réseau" value={up} accent="brand" />
          <StatPill label="Trait. serveur" value={srv} accent="violet" />
          <StatPill label="Global" value={overall} accent="emerald" highlight />
        </div>
        <div className="relative mx-auto flex size-32 items-center justify-center">
          <div
            className="absolute inset-0 rounded-full transition-[background] duration-500"
            style={{
              background: `conic-gradient(rgb(99 102 241) ${overall}%, rgba(148,163,184,0.18) ${overall}% 100%)`,
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
          <AnimatedNumber value={overall} className="tabular-nums font-semibold" suffix="%" /> terminé
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
              aria-label="Précédent"
              onClick={goPrev}
              className="absolute left-2 top-1/2 z-10 flex size-7 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-slate-600 shadow ring-1 ring-slate-200 transition hover:bg-white hover:text-brand-600 dark:bg-slate-900/90 dark:text-slate-300 dark:ring-slate-700 dark:hover:text-brand-400"
            >
              <span aria-hidden="true" className="-mt-0.5 text-base leading-none">‹</span>
            </button>
            <button
              type="button"
              aria-label="Suivant"
              onClick={goNext}
              className="absolute right-2 top-1/2 z-10 flex size-7 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-slate-600 shadow ring-1 ring-slate-200 transition hover:bg-white hover:text-brand-600 dark:bg-slate-900/90 dark:text-slate-300 dark:ring-slate-700 dark:hover:text-brand-400"
            >
              <span aria-hidden="true" className="-mt-0.5 text-base leading-none">›</span>
            </button>
            <div
              key={`${slide}-${slideDir}`}
              className={`absolute inset-0 flex flex-col justify-center px-12 text-left ${
                slideDir > 0
                  ? "motion-safe:animate-slide-in-right"
                  : "motion-safe:animate-slide-in-left"
              }`}
            >
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-brand-600 dark:text-brand-400">
                <span>{current.kicker}</span>
                {slide === activeIdx && current.kicker !== "Astuce" ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-brand-100 px-2 py-0.5 text-[9px] font-bold text-brand-700 dark:bg-brand-900/60 dark:text-brand-200">
                    <span className="size-1.5 rounded-full bg-brand-500 motion-safe:animate-pulse" />
                    En cours
                  </span>
                ) : slide < activeIdx && current.kicker !== "Astuce" ? (
                  <span className="text-emerald-600 dark:text-emerald-400">✓ Terminé</span>
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
                aria-label={`Aller à : ${s.title}`}
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
            Temps Whisper côté API ·{" "}
            {live.whisperElapsedSec != null ? `${Math.round(live.whisperElapsedSec)} s écoulés` : "…"}
            {live.whisperExpectedSec != null ? (
              <>
                {" "}
                / ≈ {Math.round(live.whisperExpectedSec)} s attendus pour ce média
              </>
            ) : null}
          </p>
        ) : null}

        {live.previewText ? (
          <div className="relative mx-auto mt-6 max-h-48 overflow-y-auto rounded-2xl border border-slate-200/90 bg-white/75 p-4 text-left text-xs leading-relaxed text-slate-800 shadow-inner dark:border-slate-700 dark:bg-slate-950/50 dark:text-slate-100">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">
              Aperçu texte brut (finalisation ensuite)
            </div>
            <p className="whitespace-pre-wrap">{live.previewText}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

const GENERATION_STEPS = [
  "Lecture du transcript et du thème",
  "Structuration des chapitres et glossaire",
  "Création du quiz et des fiches",
  "Mise en forme finale",
];

function GeneratingSkeleton() {
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setIdx((i) => (i + 1) % GENERATION_STEPS.length), 1400);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="mx-auto w-full max-w-xl space-y-6">
      <div className="space-y-2">
        <div className="h-4 w-56 animate-pulse rounded-lg bg-slate-200 dark:bg-slate-700" />
        <div className="h-3 max-w-md animate-pulse rounded-lg bg-slate-200 dark:bg-slate-700" />
      </div>
      <div className="relative overflow-hidden rounded-3xl border border-brand-200/80 bg-gradient-to-br from-brand-50 via-white to-violet-50/60 p-8 text-center shadow-soft dark:border-brand-900/50 dark:from-slate-950 dark:via-slate-900 dark:to-violet-950/30">
        <div className="pointer-events-none absolute -right-20 -top-20 h-40 w-40 rounded-full bg-brand-400/20 blur-3xl dark:bg-brand-600/10" />
        <div className="relative text-4xl">✨</div>
        <h2 className="relative font-display text-xl font-bold text-slate-900 dark:text-white">
          Génération de ton cours…
        </h2>
        <p className="relative mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-400">
          {ENGINE_COURSE} assemble glossaire, chapitres, exemples, quiz et fiches dans un parcours clair.
        </p>
        <div className="mx-auto mt-4 h-2 max-w-xs overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
          <div
            className="h-full rounded-full bg-gradient-to-r from-brand-600 via-fuchsia-500 to-brand-400 transition-[width] duration-500"
            style={{ width: `${25 + (idx + 1) * 18}%` }}
          />
        </div>
        <div className="relative mt-6 space-y-3 text-left text-sm text-slate-700 dark:text-slate-300">
          {GENERATION_STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-3">
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
  const [dark, setDark] = useDarkModeToggle();
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

  const [phase, setPhase] = useState("upload");
  const [files, setFiles] = useState([]);
  const [subject, setSubject] = useState("");
  const [speechLanguage, setSpeechLanguage] = useState("fr");
  const [transcript, setTranscript] = useState("");
  const [transcriptMixedView, setTranscriptMixedView] = useState(null);
  const [language, setLanguage] = useState("");
  const [wordCount, setWordCount] = useState(0);
  const [durationMinutes, setDurationMinutes] = useState(0);
  const [primaryName, setPrimaryName] = useState("");
  const [lesson, setLesson] = useState("");
  const [activeTab, setActiveTab] = useState("lesson");
  const [batchProgress, setBatchProgress] = useState({ perFile: [], overallPct: 0 });
  const [transcribeLive, setTranscribeLive] = useState(null);
  const [celebrate, setCelebrate] = useState(false);
  const [sessionUsage, setSessionUsage] = useState(() => ({ ...INITIAL_SESSION_USAGE }));
  const [historyBump, reloadHistoryList] = useReducer((x) => x + 1, 0);
  const [currentHistoryId, setCurrentHistoryId] = useState(null);

  const historyItems = useMemo(() => loadHistory(), [historyBump]);

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
          if (lite.status !== "done") continue;

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
                : "Audio";

          prependEntry(
            historyEntryFromTranscribePayload(result, {
              historyId: hid,
              filenames: [fn],
              subject: typeof full.subject === "string" ? full.subject : "General",
              speechLanguage: full.speech_language === "ar" ? "ar" : "fr",
            }),
          );
          forgetBgTranscribeJobId(jobId);
          reloadHistoryList();
          void reloadCreditHud();
          window.dispatchEvent(
            new CustomEvent("lecturai-toast", {
              detail: { msg: `Transcription prête (« ${fn} ») — ajoutée à l’historique.`, type: "success" },
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
  }, [authGate.ready, reloadCreditHud]);

  const busy = phase === "transcribing" || phase === "generating";

  const goUpload = () => {
    setPhase("upload");
    setSpeechLanguage("fr");
    setFiles([]);
    setBatchProgress({ perFile: [], overallPct: 0 });
    setTranscribeLive(null);
    setSessionUsage({ ...INITIAL_SESSION_USAGE });
    setTranscriptMixedView(null);
    setCurrentHistoryId(null);
  };

  const openHistoryEntry = (entry) => {
    if (!entry?.id) return;
    const u = entry.usage || {};
    setTranscript(entry.transcript || "");
    setSubject(entry.subject || "");
    setSpeechLanguage(entry.speechLanguage === "ar" ? "ar" : "fr");
    setLanguage(entry.language || "");
    setWordCount(entry.wordCount || 0);
    setDurationMinutes(entry.durationMinutes ?? 0);
    setPrimaryName(entry.displayTitle || "");
    setLesson(entry.lesson || "");
    setTranscriptMixedView(entry.transcriptMixedView ?? null);
    setSessionUsage({
      whisperAudioSeconds: u.whisperAudioSeconds ?? 0,
      whisperBilledMru: u.whisperBilledMru ?? 0,
      whisperApiEstimatedTokensSum: u.whisperApiEstimatedTokensSum ?? 0,
      claudeInput: u.claudeInput ?? 0,
      claudeOutput: u.claudeOutput ?? 0,
      claudeBilledMru: u.claudeBilledMru ?? 0,
      mixedLangPromptTokens: u.mixedLangPromptTokens ?? 0,
      mixedLangCompletionTokens: u.mixedLangCompletionTokens ?? 0,
    });
    setCurrentHistoryId(entry.id);
    const hasLesson = Boolean(entry.lesson && String(entry.lesson).trim());
    setActiveTab(hasLesson ? "lesson" : "transcript");
    setPhase(hasLesson ? "lesson" : "editing");
    window.dispatchEvent(
      new CustomEvent("lecturai-toast", { detail: { msg: "Session restaurée depuis l'historique.", type: "info" } }),
    );
  };

  const deleteHistoryEntry = (id) => {
    removeEntry(id);
    reloadHistoryList();
    if (id === currentHistoryId) setCurrentHistoryId(null);
    window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg: "Entrée supprimée.", type: "success" } }));
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
    const t = setTimeout(() => {
      const wc = transcript.trim()
        ? transcript.trim().split(/\s+/).filter(Boolean).length
        : 0;
      updateEntry(currentHistoryId, { transcript, wordCount: wc, subject });
      setWordCount(wc);
      reloadHistoryList();
    }, 1400);
    return () => clearTimeout(t);
  }, [transcript, subject, phase, currentHistoryId]);

  const exportBaseName = useMemo(() => {
    if (primaryName) return primaryName.replace(/\.[^/.]+$/, "");
    if (files[0]?.name) return files[0].name.replace(/\.[^/.]+$/, "");
    return "lecture";
  }, [primaryName, files]);

  const startTranscription = async () => {
    if (files.length === 0) return;
    setPhase("transcribing");
    const n = files.length;
    const perFile = Array(n).fill(0);
    setBatchProgress({ perFile: [...perFile], overallPct: 0 });
    setTranscribeLive({
      uploadFrac: 0,
      uploadFinished: false,
      serverFrac: 0,
      phase: "",
      message: `Prêt à envoyer ${n > 1 ? "les fichiers" : "le fichier"}…`,
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
      setSessionUsage({ ...INITIAL_SESSION_USAGE });
      setTranscriptMixedView(null);
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

      for (let i = 0; i < n; i += 1) {
        const f = files[i];
        setTranscribeLive((prev) => ({
          ...(prev || {}),
          previewText: "",
          phase: "",
          serverFrac: 0,
          message: `${i + 1}/${n}${n > 1 ? ` — ${f.name}` : ""} — préparation à l’envoi…`,
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
                : prev?.message ?? "Traitement sur le serveur…",
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

        /** @type {Record<string, unknown>} */
        let data;
        try {
          const enq = await enqueueTranscribeJobWithXHR(f, subject, speechLanguage, {
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
                message: prev?.message || "Serveur analyse le média…",
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
            throw new Error("Réponse transcription vide après traitement serveur.");
          }
          data = /** @type {Record<string, unknown>} */ (rawResult);
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
            i + 1 < n ? `Bloc ${i + 1}/${n} terminé — suivant…` : "Transcription terminée.",
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

        const label = data.filename || f.name;
        chunks.push(`=== ${label} ===\n\n${data.timestamped_transcript || data.transcript}`);
        mixedPieces.push({ label, view: data.transcript_mixed_view ?? null });
        langs.push(data.language || "");
        minutes += data.duration_minutes || 0;
      }

      const langDisplay = langs.every((l) => l && l === langs[0]) ? langs[0] || "—" : "Mixed / auto";

      const transcriptJoined = chunks.join("\n\n---\n\n");
      const mergedMv = mergeTranscriptMixedViews(mixedPieces);
      /** Texte officiel : vue unifiée (surlignages rouge / violet) si le backend l’a renvoyée, sinon verbatim horodaté. */
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

      const usageSnapshot = {
        whisperAudioSeconds: wSec,
        whisperBilledMru: wMru,
        whisperApiEstimatedTokensSum: wTok,
        claudeInput: 0,
        claudeOutput: 0,
        claudeBilledMru: 0,
        mixedLangPromptTokens: mixPrompt,
        mixedLangCompletionTokens: mixComp,
      };
      setSessionUsage(usageSnapshot);

      const hid = newHistoryId();
      prependEntry({
        id: hid,
        createdAt: new Date().toISOString(),
        displayTitle: files[0]?.name || "Sans titre",
        filenames: files.map((f) => f.name),
        transcript: transcriptFinal,
        transcriptMixedView: mergedMv ?? null,
        subject,
        speechLanguage,
        language: langDisplay,
        wordCount: wc,
        durationMinutes: minutes,
        usage: { ...usageSnapshot },
        lesson: null,
      });
      setCurrentHistoryId(hid);
      reloadHistoryList();

      setPhase("editing");
      void reloadCreditHud();
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: "Transcription prête — tu peux la corriger avant de générer le cours.", type: "success" },
        }),
      );
    } catch (e) {
      setTranscriptMixedView(null);
      const interrupted = isUploadInterruptedError(e);
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: {
            msg: interrupted
              ? e?.message ||
                "Ton envoi s’est arrêté : garde cet onglet ouvert jusqu’à la fin, puis réessaie."
              : e?.message || "La transcription n’a pas abouti. Réessaie ou choisis un autre fichier audio.",
            type: interrupted ? "info" : "error",
          },
        }),
      );
      setPhase("upload");
    }
  };

  const startGeneration = async () => {
    if (!transcript.trim() || transcript.trim().length < 50) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: {
            msg: "Le texte est encore trop court pour générer un cours complet.",
            type: "error",
          },
        }),
      );
      return;
    }
    setPhase("generating");
    try {
      const result = await generateLesson(transcript, subject || "General");
      const gu = result.usage || {};
      const cin = gu.claude_input_tokens ?? result.input_tokens ?? 0;
      const cout = gu.claude_output_tokens ?? result.output_tokens ?? 0;
      const cmru = Number(gu.billed_mru_claude ?? 0);

      const wcLesson = transcript.trim()
        ? transcript.trim().split(/\s+/).filter(Boolean).length
        : 0;

      setLesson(result.lesson || "");
      setWordCount(wcLesson);

      setSessionUsage((prev) => ({
        ...prev,
        claudeInput: cin,
        claudeOutput: cout,
        claudeBilledMru: cmru,
      }));

      if (currentHistoryId) {
        updateEntry(currentHistoryId, {
          transcript,
          subject,
          language,
          wordCount: wcLesson,
          lesson: result.lesson || "",
          durationMinutes,
          usage: {
            ...(loadHistory().find((e) => e.id === currentHistoryId)?.usage || {}),
            claudeInput: cin,
            claudeOutput: cout,
            claudeBilledMru: cmru,
          },
        });
        reloadHistoryList();
      }

      setActiveTab("lesson");
      setPhase("lesson");
      void reloadCreditHud();
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: "Ton cours est prêt ! 🎉", type: "success" },
        }),
      );
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: {
            msg: e?.message || "La génération du cours a échoué. Réessaie dans un instant.",
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
        detail: { msg: "Transcript exporté (.txt).", type: "success" },
      }),
    );
  };

  if (authGate.loading) {
    return (
      <div className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden bg-gradient-to-b from-slate-50 via-indigo-50/40 to-white dark:from-slate-950 dark:via-indigo-950/20 dark:to-slate-950">
        <div className="pointer-events-none absolute inset-0 bg-dot-grid opacity-50 motion-reduce:hidden dark:opacity-30" aria-hidden />
        <div className="glass-panel relative z-10 flex flex-col items-center gap-4 rounded-3xl px-10 py-12 shadow-soft-lg motion-safe:animate-fade-in-up">
          <div className="size-11 animate-spin rounded-full border-4 border-brand-600/25 border-t-brand-600 dark:border-brand-400/20 dark:border-t-brand-400" />
          <p className="text-sm font-medium text-slate-600 dark:text-slate-400">Vérification de l’accès…</p>
        </div>
      </div>
    );
  }

  if (authGate.loadError) {
    return (
      <div className="relative flex min-h-[100dvh] flex-col items-center justify-center gap-4 overflow-hidden bg-gradient-to-b from-slate-50 via-rose-50/30 to-white px-4 text-center dark:from-slate-950 dark:via-rose-950/10 dark:to-slate-950">
        <div className="pointer-events-none absolute inset-0 bg-dot-grid opacity-40 dark:opacity-25" aria-hidden />
        <p className="relative z-10 max-w-md text-sm text-slate-700 dark:text-slate-300">
          Impossible de joindre l’API (configuration d’authentification). Lance le backend avec{" "}
          <code className="rounded bg-slate-200 px-1 py-0.5 text-xs dark:bg-slate-800">uvicorn</code> et réessaie.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="relative z-10 rounded-2xl bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white shadow-glow hover:brightness-105"
        >
          Recharger
        </button>
      </div>
    );
  }

  if (!authGate.skipped && !authGate.ready) {
    return (
      <div className="relative flex min-h-[100dvh] min-w-0 flex-col overflow-x-hidden bg-gradient-to-b from-slate-50 via-white to-indigo-50/30 text-slate-900 transition dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/25 dark:text-slate-50">
        <div className="pointer-events-none fixed inset-0 bg-dot-grid opacity-40 dark:opacity-[0.2]" aria-hidden />
        <div className="pointer-events-none fixed inset-0 overflow-hidden">
          <div className="absolute -left-[18%] top-[-12%] h-[min(400px,70vw)] w-[min(400px,95vw)] rounded-full bg-brand-400/26 blur-[100px] motion-safe:animate-blob-drift dark:bg-indigo-600/16" />
          <div className="absolute bottom-[-18%] right-[-12%] h-[min(380px,65vw)] w-[min(380px,92vw)] rounded-full bg-cyan-400/24 blur-[110px] motion-safe:animate-blob-drift-reverse dark:bg-cyan-600/14" />
        </div>
        <ToastPortal />
        <AuthScreen onAuthed={handleAuthed} />
      </div>
    );
  }

  if (authGate.ready && !authGate.skipped && profileFlags.is_admin) {
    const adminEmail = getAuthProfile()?.email;
    return (
      <div className="relative flex min-h-[100dvh] min-w-0 flex-col overflow-x-hidden bg-gradient-to-b from-slate-50 via-emerald-50/[0.2] to-cyan-50/25 text-slate-900 transition dark:from-slate-950 dark:via-emerald-950/[0.08] dark:to-slate-950 dark:text-slate-50">
        <div className="pointer-events-none fixed inset-0 bg-dot-grid opacity-35 dark:opacity-[0.18]" aria-hidden />
        <div className="pointer-events-none fixed inset-0 overflow-hidden">
          <div className="absolute -left-[15%] top-[-14%] h-[min(400px,70vw)] w-[min(400px,92vw)] rounded-full bg-emerald-400/24 blur-[100px] motion-safe:animate-blob-drift dark:bg-emerald-600/12" />
          <div className="absolute bottom-[-14%] right-[-12%] h-[min(400px,60vw)] w-[min(400px,85vw)] rounded-full bg-cyan-400/20 blur-[110px] motion-safe:animate-blob-drift-reverse" />
        </div>

        <ToastPortal />
        <header className="sticky top-0 z-50 px-3 pt-3 pb-1 sm:px-6">
          <div className="glass-header mx-auto flex min-w-0 max-w-full flex-wrap items-center justify-between gap-3 rounded-2xl px-4 py-3 sm:max-w-5xl sm:rounded-3xl sm:px-6">
            <div className="flex flex-col">
              <span className="font-display text-xl font-extrabold tracking-tight">
                <span className="text-gradient-brand">LecturAI</span>{" "}
                <span className="align-middle text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-slate-400">
                  admin
                </span>
              </span>
              <span className="mt-0.5 text-[11px] leading-snug text-slate-500 dark:text-slate-400">
                Validation des demandes de recharge — aucun autre accès pour ce compte.
              </span>
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <WhatsAppSupportButton variant="admin" />
              {adminEmail ? (
                <span
                  title={adminEmail}
                  className="hidden max-w-[14rem] truncate text-[10px] font-medium text-slate-500 sm:inline dark:text-slate-400"
                >
                  {adminEmail}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  clearAuthSession();
                  setProfileFlags({ is_admin: false });
                  setAuthGate({ loading: false, skipped: false, ready: false, loadError: false });
                }}
                className="rounded-full border border-rose-200/80 bg-rose-500/10 px-3.5 py-2 text-[11px] font-semibold text-rose-800 shadow-sm transition hover:bg-rose-500/15 dark:border-rose-900/60 dark:text-rose-200 dark:hover:bg-rose-950/50"
              >
                Déconnexion
              </button>
              <button
                type="button"
                onClick={() => setDark((d) => !d)}
                className="rounded-full border border-slate-200/90 bg-white/60 px-3.5 py-2 text-[11px] font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-100 dark:hover:bg-slate-800"
                aria-label="Basculer thème"
              >
                {dark ? "☀️ Clair" : "🌙 Sombre"}
              </button>
            </div>
          </div>
        </header>

        <main className="relative z-10 mx-auto w-full min-w-0 flex-1 max-w-5xl safe-pad-x px-4 py-8 sm:py-12 lg:px-8 lg:py-14">
          <AdminTopUpsPage />
        </main>

        <footer className="relative z-10 mx-auto max-w-5xl px-4 pb-8 text-center text-[11px] text-slate-500 dark:text-slate-500">
          Connexion réservée aux comptes utilisateur sur un autre navigateur ou appareil pour transcrire ou générer des cours.
        </footer>
      </div>
    );
  }

  const profileEmail = getAuthProfile()?.email;
  const badges = phaseBadgeLabels(phase);

  return (
    <div className="relative flex min-h-[100dvh] min-w-0 flex-col overflow-x-hidden bg-gradient-to-b from-slate-50 via-white to-indigo-50/25 text-slate-900 transition dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/30 dark:text-slate-50">
      <div className="pointer-events-none fixed inset-0 bg-dot-grid opacity-[0.45] dark:opacity-[0.22]" aria-hidden />
      {/* Blobs animés : masqués < lg pour alléger le GPU mobile (zones du bas qui “arrivent” en retard). */}
      <div className="pointer-events-none fixed inset-0 hidden overflow-hidden lg:block" aria-hidden>
        <div className="absolute -left-[20%] top-[-14%] h-[min(460px,60vw)] w-[min(460px,85vw)] rounded-full bg-brand-400/28 blur-[100px] motion-safe:animate-blob-drift dark:bg-indigo-600/18" />
        <div className="absolute bottom-[-18%] right-[-14%] h-[min(440px,55vw)] w-[min(440px,82vw)] rounded-full bg-cyan-400/22 blur-[110px] motion-safe:animate-blob-drift-reverse dark:bg-cyan-600/14" />
        <div className="absolute left-1/2 top-[38%] h-72 w-72 -translate-x-1/2 rounded-full bg-violet-400/18 blur-[100px] motion-safe:animate-breathe dark:bg-violet-600/10" />
      </div>

      <ToastPortal />
      <header className="sticky top-0 z-50 px-3 pt-3 pb-1 sm:px-4 lg:px-8">
        <div className="glass-header mx-auto min-w-0 max-w-full rounded-2xl px-4 py-3 sm:max-w-6xl sm:rounded-3xl sm:px-6 sm:py-3.5">
          <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between lg:items-center">
            <div className="flex min-w-0 flex-1 items-start gap-3">
              <div className="min-w-0">
                <span className="flex flex-wrap items-baseline gap-2 font-display">
                  <span className="text-fluid-hero leading-none font-extrabold tracking-tight text-slate-900 dark:text-white">
                    <span className="text-gradient-brand">LecturAI</span>
                  </span>
                  <span className="inline-flex shrink-0 items-center rounded-full border border-brand-500/25 bg-brand-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.22em] text-brand-700 dark:border-brand-400/35 dark:bg-brand-500/15 dark:text-brand-200">
                    beta
                  </span>
                  <span
                    className="inline-flex shrink-0 items-center rounded-full border border-slate-200/90 bg-white/85 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-600 shadow-sm dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-300 sm:hidden"
                    aria-live="polite"
                  >
                    {badges.short}
                  </span>
                </span>
                <p className="mt-2 max-w-md text-[12px] leading-relaxed text-slate-600 dark:text-slate-400 sm:text-[13px]">
                  De l&apos;audio au cours structuré — sans friction.
                </p>
              </div>
            </div>

            <div className="flex w-full min-w-0 flex-wrap items-center gap-2 justify-start sm:w-auto sm:justify-end">
              <span className="hidden items-center rounded-full border border-transparent bg-slate-900/[0.04] px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500 dark:bg-white/[0.04] dark:text-slate-400 sm:inline-flex">
                {badges.full}
              </span>
              {!authGate.skipped ? (
                <button
                  type="button"
                  disabled={busy}
                  title={
                    creditHud?.blocked && !creditHud.canUse ? creditHud.blocked : "Solde MRU — portefeuille et recharge"
                  }
                  onClick={() => setPhase("credits")}
                  className={`rounded-full border px-3 py-2 text-[11px] font-bold shadow-sm backdrop-blur-sm transition hover:brightness-[1.02] disabled:cursor-not-allowed disabled:opacity-45 tabular-nums sm:px-4 ${
                    creditHud && !creditHud.canUse
                      ? "border-amber-300/70 bg-gradient-to-br from-amber-100 to-amber-50/70 text-amber-950 shadow-amber-200/30 dark:border-amber-900/60 dark:from-amber-950/60 dark:to-amber-950/30 dark:text-amber-100"
                      : "border-violet-300/55 bg-gradient-to-br from-white to-violet-50/80 text-violet-900 shadow-violet-400/15 dark:border-violet-900/65 dark:from-violet-950/50 dark:to-slate-900/85 dark:text-violet-100"
                  }`}
                >
                  <span className="opacity-85">MRU</span>
                  <span>{creditHud != null ? ` · ${creditHud.balance}` : ""}</span>
                </button>
              ) : null}
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  reloadHistoryList();
                  setPhase("history");
                }}
                className="rounded-full border border-slate-200/90 bg-white/75 px-3 py-2 text-[11px] font-bold text-slate-800 shadow-sm backdrop-blur-md transition hover:border-slate-300 hover:bg-white disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800 sm:px-4"
              >
                Historique
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
                  className="rounded-full border border-rose-300/60 bg-white/85 px-3 py-2 text-[11px] font-bold text-rose-800 shadow-sm transition hover:bg-rose-50 dark:border-rose-900/55 dark:bg-rose-950/40 dark:text-rose-200 dark:hover:bg-rose-950/70 sm:px-4"
                >
                  Déconnexion
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => setDark((d) => !d)}
                className="inline-flex items-center rounded-full border border-slate-200/90 bg-slate-900/[0.03] px-3 py-2 text-[11px] font-semibold text-slate-700 transition hover:bg-white dark:border-slate-600 dark:bg-white/[0.05] dark:text-slate-200 dark:hover:bg-slate-800"
                aria-label="Basculer thème"
              >
                <span aria-hidden>{dark ? "☀️" : "🌙"}</span>
                <span className="ml-1 hidden sm:inline">{dark ? "Clair" : "Sombre"}</span>
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto w-full min-w-0 flex-1 max-w-6xl safe-pad-x px-4 py-8 pb-14 sm:px-6 lg:px-10 lg:py-12 xl:pb-20">
        {phase === "history" && (
          <TranscriptionHistoryPage
            items={historyItems}
            onBack={() => setPhase("upload")}
            onOpen={openHistoryEntry}
            onDelete={deleteHistoryEntry}
          />
        )}

        {phase === "credits" && <CreditsPage onBack={() => setPhase("upload")} onWalletUpdated={reloadCreditHud} />}

        {phase === "upload" && (
          <>
            <UploadZone
              files={files}
              onFilesChange={setFiles}
              subject={subject}
              onSubjectChange={setSubject}
              speechLanguage={speechLanguage}
              onSpeechLanguageChange={setSpeechLanguage}
              disabled={busy}
              batchProgress={batchProgress}
              onSubmit={startTranscription}
            />
            <BgTranscribeJobsPanel authReady={authGate.ready} />
          </>
        )}

        {phase === "transcribing" && (
          <>
            {files.length > 0 && (
              <section className="glass-panel mx-auto mb-8 w-full max-w-xl space-y-3 rounded-3xl p-5 shadow-soft">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-400">
                  Envoi et traitement
                </div>
                <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
                  Barre globale : {TRANSCRIBE_UPLOAD_WEIGHT * 100}% envoi fichier + {TRANSCRIBE_SERVER_WEIGHT * 100}%
                  analyse serveur (Whisper puis finalisation).
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
                            Global {Math.round(p * 100)}%
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
                  Peaufine ton transcript
                </h2>
                <p className="max-w-xl text-sm leading-relaxed text-slate-600 dark:text-slate-400">
                  {ENGINE_TRANSCRIPTION} peut se tromper sur les noms propres ou le jargon : corrige le texte ici avant
                  que{" "}
                  {ENGINE_COURSE} ne construise le cours.
                </p>
              </div>
              <button
                type="button"
                onClick={goUpload}
                className="rounded-2xl border border-slate-200/90 bg-white/70 px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
              >
                ← Nouvel import
              </button>
            </div>

            <TranscriptEditor
              value={transcript}
              onChange={setTranscript}
              language={language}
              speechLanguageChosen={speechLanguage === "ar" ? "arabe" : "français"}
              wordCount={wordCount}
              durationMinutes={durationMinutes}
              primaryFileName={primaryName}
              onExportTxt={exportTxt}
              usage={sessionUsage}
              mixedView={transcriptMixedView}
            />

            <p className="text-xs leading-relaxed text-slate-500 dark:text-slate-500">
              Les montants MRU sont indicatifs pour cette session. Les détails de la génération du cours figureront après
              l&apos;étape suivante.
            </p>

            <div className="glass-panel rounded-3xl border border-brand-200/60 bg-gradient-to-br from-brand-50/90 via-white to-violet-50/50 p-8 text-center shadow-soft dark:border-brand-900/40 dark:from-slate-900/80 dark:via-slate-900 dark:to-violet-950/20">
              <h3 className="font-display text-xl font-bold text-slate-900 dark:text-white">
                ✨ Passer au cours structuré
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-600 dark:text-slate-400">
                {ENGINE_COURSE} produit glossaire, chapitres avec exemples, tableau récapitulatif, quiz et fiches —
                exportables quand tu veux.
              </p>
              <button
                type="button"
                onClick={startGeneration}
                disabled={busy}
                className="mt-6 inline-flex items-center gap-2 rounded-2xl bg-gradient-to-r from-brand-600 via-brand-500 to-violet-600 px-8 py-3.5 text-sm font-bold text-white shadow-glow transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none dark:disabled:opacity-40"
              >
                ✨ Générer le cours
              </button>
            </div>
          </div>
        )}

        {phase === "generating" && <GeneratingSkeleton />}

        {phase === "lesson" && (
          <LessonViewer
            activeTab={activeTab}
            onTabChange={setActiveTab}
            transcript={transcript}
            transcriptMixedView={transcriptMixedView}
            lesson={lesson}
            subject={subject || "General"}
            filename={exportBaseName}
            language={language}
            speechChosenLabel={speechLanguage === "ar" ? "Arabe" : "Français"}
            wordCount={wordCount}
            durationMinutes={durationMinutes}
            celebration={celebrate}
            usage={sessionUsage}
          />
        )}

        {phase === "lesson" && (
          <div className="mx-auto mt-10 flex max-w-3xl flex-wrap justify-between gap-3">
            <button
              type="button"
              onClick={() => {
                setLesson("");
                setPhase("editing");
              }}
              className="rounded-2xl border border-slate-200/90 bg-white/60 px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              ← Retour au transcript
            </button>
            <button
              type="button"
              onClick={() => {
                setLesson("");
                setTranscript("");
                setTranscriptMixedView(null);
                setFiles([]);
                setSpeechLanguage("fr");
                setPhase("upload");
                setBatchProgress({ perFile: [], overallPct: 0 });
                setSessionUsage({ ...INITIAL_SESSION_USAGE });
                setCurrentHistoryId(null);
              }}
              className="rounded-2xl border border-transparent px-4 py-2.5 text-sm font-semibold text-brand-700 transition hover:bg-brand-500/10 dark:text-brand-300 dark:hover:bg-brand-950"
            >
              Nouvelle session
            </button>
          </div>
        )}
      </main>

      <footer className="relative z-10 mx-4 mb-6 mt-auto rounded-3xl border border-white/50 bg-white/45 px-4 py-6 text-center text-xs leading-relaxed text-slate-500 shadow-sm backdrop-blur-md dark:border-slate-700/70 dark:bg-slate-950/55 dark:text-slate-400 sm:mx-auto sm:max-w-6xl sm:px-6 safe-pad-b">
        Conçu pour de longues séances de révision — interface pensée tactile et clavier, sur tous les écrans.
      </footer>
    </div>
  );
}
