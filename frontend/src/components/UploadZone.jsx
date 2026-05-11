import { memo, useEffect, useRef, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import {
  ENGINE_COURSE,
  ENGINE_TRANSCRIBE_ATELIER,
  ENGINE_TRANSCRIBE_CLOUD,
  ENGINE_TRANSCRIPTION,
} from "../branding.js";

/** Extensions audio uniquement (pas vidéo PDF image). */
const AUDIO_EXTENSIONS = new Set([
  ".mp3",
  ".wav",
  ".m4a",
  ".mp4",
  ".m4b",
  ".ogg",
  ".oga",
  ".opus",
  ".flac",
  ".aac",
  ".caf",
  ".aiff",
  ".aif",
  ".wma",
  ".amr",
]);
const ACCEPT = Array.from(AUDIO_EXTENSIONS).join(",");
const AUDIO_EXTENSIONS_LABEL = Array.from(AUDIO_EXTENSIONS)
  .map((e) => e.slice(1).toUpperCase())
  .sort()
  .join(", ");

/** Limite côté navigateur pour l’estimation durée ; le serveur impose sa propre durée max (TRANSCRIBE_MAX_DURATION_SECONDS). */
const MAX_AUDIO_SECONDS = 2 * 3600;

function audioExtension(name) {
  if (!name || typeof name !== "string") return "";
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function formatDur(sec, dash) {
  if (!sec || !Number.isFinite(sec) || sec <= 0) return dash;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function useEstimatedDurations(files) {
  const [map, setMap] = useState({});
  const started = useRef(new Set());

  useEffect(() => {
    let cancelled = false;
    const wanted = new Set(files.map((f) => `${f.name}-${f.size}`));

    for (const key of [...started.current]) {
      if (!wanted.has(key)) started.current.delete(key);
    }

    setMap((prev) => {
      const next = { ...prev };
      for (const k of Object.keys(next)) {
        if (!wanted.has(k)) delete next[k];
      }
      return next;
    });

    for (const f of files) {
      const key = `${f.name}-${f.size}`;
      if (started.current.has(key)) continue;
      started.current.add(key);

      const url = URL.createObjectURL(f);
      const audio = new Audio();
      audio.preload = "metadata";
      audio.src = url;

      const cleanupUrl = () => URL.revokeObjectURL(url);

      const onMeta = () => {
        if (cancelled) return;
        const d = audio.duration;
        setMap((p) => ({
          ...p,
          [key]: Number.isFinite(d) ? d : null,
        }));
        cleanupUrl();
      };
      const onErr = () => {
        if (cancelled) return;
        setMap((p) => ({ ...p, [key]: null }));
        cleanupUrl();
      };

      audio.addEventListener("loadedmetadata", onMeta, { once: true });
      audio.addEventListener("error", onErr, { once: true });
    }

    return () => {
      cancelled = true;
    };
  }, [files]);

  return map;
}

function UploadZone({
  files,
  onFilesChange,
  subject,
  onSubjectChange,
  speechLanguage = "fr",
  onSpeechLanguageChange,
  transcriptionEngine = "openai",
  onTranscriptionEngineChange,
  onSubmit,
  disabled,
  batchProgress,
}) {
  const { t } = useTranslation();
  const durations = useEstimatedDurations(files);
  const [drag, setDrag] = useState(false);
  const dash = t("common.dash");

  useEffect(() => {
    if (files.length === 0 || disabled) return;
    const namesTooLong = [];
    const next = [];
    for (const f of files) {
      const key = `${f.name}-${f.size}`;
      const raw = durations[key];
      const sec = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
      if (sec != null && sec > MAX_AUDIO_SECONDS) namesTooLong.push(f.name);
      else next.push(f);
    }
    if (namesTooLong.length === 0) return;
    const label =
      namesTooLong.length <= 2
        ? namesTooLong.map((n) => n.slice(0, 64)).join(", ")
        : t("upload.filesN", { n: namesTooLong.length });
    window.dispatchEvent(
      new CustomEvent("lecturai-toast", {
        detail: {
          msg: t("upload.toastTooLong", {
            label,
            h: MAX_AUDIO_SECONDS / 3600,
          }),
          type: "error",
        },
      }),
    );
    onFilesChange(next);
  }, [files, durations, disabled, onFilesChange]);

  const addFiles = (list) => {
    const arr = Array.from(list || []);
    const accepted = [];
    const rejected = [];
    for (const f of arr) {
      if (AUDIO_EXTENSIONS.has(audioExtension(f.name))) accepted.push(f);
      else rejected.push(f.name || t("upload.unnamedFile"));
    }
    if (rejected.length) {
      const sample =
        rejected.length <= 2 ? rejected.join(", ") : `${t("upload.filesN", { n: rejected.length })} (${rejected[0].slice(0, 48)}…)`;
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: {
            msg: t("upload.toastRejected", { sample, ext: AUDIO_EXTENSIONS_LABEL }),
            type: "error",
          },
        }),
      );
    }
    if (accepted.length) onFilesChange([...files, ...accepted]);
  };

  const removeAt = (i) => {
    const copy = [...files];
    copy.splice(i, 1);
    onFilesChange(copy);
  };

  const pctOverall = batchProgress?.overallPct ?? 0;

  return (
    <div className="mx-auto w-full min-w-0 max-w-6xl space-y-6 pb-8 safe-pad-b sm:space-y-10 sm:pb-6 lg:pb-6">
      {/*
        Mobile : importer d’abord (ordre visual), halo marketing ensuite — évite vide au scroll + zone réglages plus haut à l’écran.
      */}
      <div className="flex flex-col gap-5 sm:gap-7 lg:grid lg:grid-cols-12 lg:gap-14 xl:gap-16">
        <header className="order-2 space-y-3 text-center sm:space-y-4 lg:order-none lg:col-span-5 lg:text-start xl:col-span-4 lg:sticky lg:top-28 lg:self-start xl:top-[7.5rem]">
          <div className="inline-flex rounded-full bg-gradient-to-r from-brand-500/15 via-amber-500/12 to-rose-500/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.26em] text-brand-900 shadow-sm shadow-brand-500/10 dark:text-brand-100 dark:shadow-none sm:px-3.5 sm:py-1.5 sm:tracking-[0.28em]">
            {ENGINE_TRANSCRIPTION} × {ENGINE_COURSE}
          </div>
          <h1 className="font-display text-[clamp(1.45rem,5.5vw+0.65rem,2.875rem)] font-extrabold leading-[1.12] tracking-tight text-slate-900 dark:text-white sm:text-fluid-hero sm:leading-[1.1]">
            {t("upload.heroLead")}{" "}
            <span className="text-gradient-brand">{t("upload.heroTitleAccent")}</span>
          </h1>
          <p className="mx-auto max-w-lg text-sm leading-relaxed text-slate-600 dark:text-slate-400 sm:text-[15px] sm:leading-[1.65] lg:mx-0 lg:max-w-none lg:text-[0.9625rem]">
            <Trans
              i18nKey="upload.heroBody"
              values={{ transcription: ENGINE_TRANSCRIPTION, course: ENGINE_COURSE }}
              components={{
                t: <span className="font-semibold text-slate-800 dark:text-slate-100" />,
                c: <span className="font-semibold text-slate-800 dark:text-slate-100" />,
              }}
            />
          </p>
          <ul className="scroll-x-contained mx-auto mt-4 flex snap-x snap-mandatory gap-2.5 overflow-x-auto scroll-pl-4 pb-1 ps-1 pe-1 text-start text-[12.5px] text-slate-600 dark:text-slate-400 sm:mt-5 sm:grid sm:snap-none sm:grid-cols-1 sm:gap-2.5 sm:overflow-visible sm:scroll-pl-0 sm:ps-0 sm:pe-0 lg:mx-0 lg:mt-6 xl:gap-4">
            <li className="flex min-w-[min(100%,18.5rem)] shrink-0 snap-start gap-2.5 rounded-2xl border border-slate-200/80 bg-gradient-to-br from-white to-slate-50/90 px-3 py-2.5 shadow-sm dark:border-slate-700/80 dark:from-slate-900/90 dark:to-slate-950/80 sm:min-w-0 sm:gap-3 sm:from-white/40 sm:to-transparent sm:dark:from-slate-900/40">
              <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-lg bg-brand-500/15 text-[11px]" aria-hidden>1</span>
              <span>
                <Trans
                  i18nKey="upload.point1"
                  values={{
                    large: t("upload.pointLargeWord"),
                    hours: MAX_AUDIO_SECONDS / 3600,
                  }}
                  components={{
                    large: <strong />,
                    hours: <strong />,
                  }}
                />
              </span>
            </li>
            <li className="flex min-w-[min(100%,18.5rem)] shrink-0 snap-start gap-2.5 rounded-2xl border border-slate-200/80 bg-gradient-to-br from-white to-amber-50/45 px-3 py-2.5 shadow-sm dark:border-slate-700/80 dark:from-slate-900/90 dark:to-amber-950/20 sm:min-w-0 sm:gap-3 sm:from-white/40 sm:to-transparent sm:dark:from-slate-900/40">
              <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-lg bg-amber-500/18 text-[11px]" aria-hidden>2</span>
              <span>
                <Trans i18nKey="upload.point2" components={{ corr: <strong /> }} />
              </span>
            </li>
          </ul>
        </header>

        <div className="order-1 min-w-0 space-y-5 sm:space-y-8 lg:order-none lg:col-span-7 xl:col-span-8">
          <div
        role="presentation"
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          addFiles(e.dataTransfer.files);
        }}
        className={`group relative isolate min-h-[13.5rem] overflow-hidden rounded-2xl border-2 border-dashed px-4 py-9 text-center transition-all duration-300 sm:min-h-0 sm:rounded-[2rem] sm:px-8 sm:py-14 lg:rounded-[2.1rem]
          ${
            drag
              ? "scale-[1.01] border-brand-500 bg-gradient-to-br from-brand-500/15 via-brand-400/10 to-amber-500/15 shadow-[0_0_0_3px_rgb(234_88_12/0.22),0_32px_64px_-24px_rgb(194_65_12/0.45)] motion-reduce:scale-100 dark:border-brand-400"
              : "border-slate-300/95 bg-gradient-to-b from-white via-white to-slate-50/90 ring-gradient-brand hover-lift motion-reduce:transform-none motion-reduce:shadow-none dark:border-slate-600 dark:from-slate-900/95 dark:via-slate-900 dark:to-slate-950/90"
          }
        `}
      >
        <div
          className={`pointer-events-none absolute inset-[2px] rounded-[calc(1.75rem-2px)] transition-opacity duration-500 sm:rounded-[calc(2rem-2px)] lg:rounded-[calc(2.1rem-2px)] motion-reduce:transition-none ${
            drag
              ? "bg-gradient-to-br from-brand-500/20 via-transparent to-amber-500/22 opacity-100"
              : "bg-gradient-to-br from-brand-500/[0.08] via-transparent to-amber-600/[0.1] opacity-0 group-hover:opacity-100 motion-reduce:opacity-70 dark:from-brand-400/15 dark:to-amber-600/16"
          }`}
          aria-hidden
        />
        <div className="pointer-events-none absolute left-10 right-10 top-[18%] h-px bg-gradient-to-r from-transparent via-brand-400/35 to-transparent opacity-75 dark:via-brand-300/35" aria-hidden />
        <input
          aria-label={t("upload.importAudio")}
          type="file"
          accept={ACCEPT}
          multiple
          disabled={disabled}
          className="absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
          onChange={(e) => addFiles(e.target.files)}
        />
        <div className="pointer-events-none relative z-[1] space-y-4">
          <div className="relative mx-auto flex h-[4.1rem] w-[4.1rem] items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500/20 to-amber-500/25 text-[1.85rem] shadow-inner shadow-brand-500/10 transition-transform duration-500 group-hover:scale-105 motion-reduce:transform-none dark:from-brand-500/25 dark:to-amber-600/30 dark:shadow-brand-900/30 sm:h-[4.75rem] sm:w-[4.75rem] sm:text-[2.05rem]">
            <span className="drop-shadow-sm" aria-hidden>
              🎙️
            </span>
            <span className="pointer-events-none absolute inset-[-4px] hidden rounded-[1rem] bg-brand-400/25 opacity-70 blur-xl motion-safe:animate-breathe dark:bg-brand-600/25 lg:block" aria-hidden />
          </div>
          <h2 className="font-display text-lg font-bold tracking-tight text-slate-900 dark:text-white sm:text-[1.35rem]">
            {t("upload.dropTitle")}
          </h2>
          <p className="text-[13px] leading-snug text-slate-500 dark:text-slate-400 sm:text-sm sm:text-[0.948rem]">
            {t("upload.dropHint", { ext: AUDIO_EXTENSIONS_LABEL, h: MAX_AUDIO_SECONDS / 3600 })}
          </p>
          <div className="scroll-x-contained mx-auto flex max-w-full flex-nowrap justify-start gap-1.5 overflow-x-auto pt-1 sm:flex-wrap sm:justify-center sm:overflow-visible sm:pt-1 md:gap-2">
            {AUDIO_EXTENSIONS_LABEL.split(", ").map((extLabel) => (
              <span
                key={extLabel}
                className="shrink-0 rounded-full border border-slate-200/90 bg-white px-2.5 py-1 text-[9px] font-bold uppercase tracking-wider text-slate-600 shadow-sm dark:border-slate-600 dark:bg-slate-900/90 dark:text-slate-300 sm:bg-white/90 sm:px-3 sm:py-1 sm:text-[11px]"
              >
                {extLabel}
              </span>
            ))}
          </div>
        </div>
          </div>

      {files.length > 0 && (
        <ul className="space-y-3">
          {files.map((f, i) => {
            const key = `${f.name}-${f.size}`;
            const d = durations[key];
            const filePct = batchProgress?.perFile?.[i] ?? 0;

            return (
              <li
                key={key}
                className="glass-panel flex flex-col gap-3 rounded-2xl p-4 shadow-sm sm:flex-row sm:items-center sm:gap-3"
              >
                <div className="flex min-w-0 flex-1 items-start gap-3 sm:items-center">
                  <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-brand-500/10 text-xl dark:bg-brand-500/15">
                    🎵
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="break-words text-sm font-semibold text-slate-900 dark:text-white sm:truncate">{f.name}</div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400">
                      <span>{formatBytes(f.size)}</span>
                      <span className="text-slate-400">•</span>
                      <span>
                        {t("upload.estDur")} {formatDur(d, dash)}
                      </span>
                      {disabled && (
                        <>
                          <span className="text-slate-400">•</span>
                          <span className="font-medium text-brand-600 dark:text-brand-400">
                            {t("upload.sendPct", { n: Math.round(filePct * 100) })}
                          </span>
                        </>
                      )}
                    </div>
                    {disabled && (
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-brand-600 to-amber-500 transition-[width]"
                          style={{ width: `${Math.round(filePct * 100)}%` }}
                        />
                      </div>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={disabled}
                  className="min-h-[2.75rem] shrink-0 self-end rounded-xl px-4 py-2.5 text-[11px] font-semibold text-slate-500 transition hover:bg-rose-50 hover:text-rose-600 active:scale-[0.98] disabled:opacity-40 motion-reduce:active:scale-100 dark:hover:bg-rose-950/40 dark:hover:text-rose-300 sm:min-h-0 sm:self-auto sm:px-3 sm:py-2"
                  onClick={() => removeAt(i)}
                >
                  {t("upload.remove")}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {disabled && files.length > 0 && (
        <div className="space-y-1">
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-brand-600 to-amber-500 transition-[width]"
              style={{ width: `${Math.round(pctOverall * 100)}%` }}
            />
          </div>
          <p className="text-center text-[11px] text-slate-500">
            {t("upload.globalProgress", { n: Math.round(pctOverall * 100) })}
          </p>
        </div>
      )}

          <div className="glass-panel relative space-y-4 overflow-hidden rounded-3xl border border-white/65 p-4 pt-5 shadow-soft sm:p-5 sm:pt-5 lg:p-6 dark:border-slate-700/80">
        <div
          className="pointer-events-none absolute inset-x-6 top-0 h-1 rounded-full bg-gradient-to-r from-brand-500 via-amber-400 to-rose-400 opacity-[0.92] lg:hidden"
          aria-hidden
        />
        <fieldset className="space-y-2">
          <legend className="text-sm font-bold text-slate-800 dark:text-slate-200">{t("upload.fieldsetLang")}</legend>
          <p className="text-[11px] leading-snug text-slate-500 dark:text-slate-400">
            <Trans
              i18nKey="upload.langHint"
              components={{
                fr: <strong className="text-slate-700 dark:text-slate-300" />,
                ar: <strong className="text-slate-700 dark:text-slate-300" />,
              }}
            />
          </p>
          <div className="grid grid-cols-1 gap-2 xs:grid-cols-2" role="group" aria-label={t("upload.langGroupAria")}>
            {[
              { id: "fr", label: t("langs.french") },
              { id: "ar", label: t("langs.arabic") },
            ].map((opt) => (
              <label
                key={opt.id}
                className={`flex min-h-[2.75rem] cursor-pointer items-center justify-center gap-2 rounded-2xl border px-4 py-2.5 text-xs font-bold transition sm:min-h-0 sm:justify-start sm:text-sm ${
                  speechLanguage === opt.id
                    ? "border-brand-500 bg-brand-500/10 text-brand-900 shadow-inner dark:border-brand-400 dark:bg-brand-950/45 dark:text-brand-100"
                    : "border-slate-200/90 bg-white/70 text-slate-700 hover:border-slate-300 dark:border-slate-600 dark:bg-slate-950/60 dark:text-slate-200"
                }`}
              >
                <input
                  type="radio"
                  className="sr-only"
                  name="speech-language"
                  checked={speechLanguage === opt.id}
                  disabled={disabled}
                  onChange={() => typeof onSpeechLanguageChange === "function" && onSpeechLanguageChange(/** @type {"fr"|"ar"} */ (opt.id))}
                  value={opt.id}
                />
                {opt.label}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="space-y-2">
          <legend className="text-sm font-bold text-slate-800 dark:text-slate-200">{t("upload.fieldsetEngine")}</legend>
          <p className="text-[11px] leading-snug text-slate-500 dark:text-slate-400">
            {t("upload.engineHint", { cloud: ENGINE_TRANSCRIBE_CLOUD, desk: ENGINE_TRANSCRIBE_ATELIER })}
          </p>
          <div className="grid grid-cols-1 gap-2 xs:grid-cols-2" role="group" aria-label={t("upload.engineGroupAria")}>
            {[
              { id: "openai", label: ENGINE_TRANSCRIBE_CLOUD },
              { id: "local", label: ENGINE_TRANSCRIBE_ATELIER },
            ].map((opt) => (
              <label
                key={opt.id}
                className={`flex min-h-[2.75rem] min-w-0 cursor-pointer items-center justify-center gap-2 rounded-2xl border px-3 py-2.5 text-center text-[11px] font-bold leading-snug transition xs:px-4 sm:min-h-0 sm:justify-start sm:text-left sm:text-sm ${
                  transcriptionEngine === opt.id
                    ? "border-brand-500 bg-brand-500/10 text-brand-900 shadow-inner dark:border-brand-400 dark:bg-brand-950/45 dark:text-brand-100"
                    : "border-slate-200/90 bg-white/70 text-slate-700 hover:border-slate-300 dark:border-slate-600 dark:bg-slate-950/60 dark:text-slate-200"
                }`}
              >
                <input
                  type="radio"
                  className="sr-only"
                  name="transcription-engine"
                  checked={transcriptionEngine === opt.id}
                  disabled={disabled}
                  onChange={() =>
                    typeof onTranscriptionEngineChange === "function" &&
                    onTranscriptionEngineChange(/** @type {"openai"|"local"} */ (opt.id))
                  }
                  value={opt.id}
                />
                <span className="break-words">{opt.label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <label className="flex flex-col gap-1.5 text-sm font-bold text-slate-800 dark:text-slate-200 sm:flex-row sm:items-center sm:justify-between sm:gap-2">
          {t("upload.subject")}
          <span className="rounded-full bg-slate-900/[0.04] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:bg-white/[0.06] dark:text-slate-400">
            {t("upload.subjectBadge")}
          </span>
        </label>
        <input
          value={subject}
          onChange={(e) => onSubjectChange(e.target.value)}
          placeholder={t("upload.subjectPh")}
          className="w-full rounded-2xl border border-slate-200/95 bg-white/90 px-4 py-3.5 text-sm outline-none shadow-inner shadow-slate-900/[0.02] ring-transparent transition-[border-color,box-shadow] focus:border-brand-500 focus:ring-[3px] focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-950/85 dark:focus:border-brand-400 dark:focus:ring-brand-400/20"
          disabled={disabled}
        />
        <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
          {t("upload.subjectHelp", { course: ENGINE_COURSE })}
        </p>
      </div>

      <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between sm:gap-6">
        <button
          type="button"
          disabled={disabled || files.length === 0}
          onClick={onSubmit}
          className="inline-flex min-h-[3.25rem] w-full items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-brand-600 via-amber-500 to-rose-500 px-6 py-3.5 text-sm font-bold text-white shadow-[0_22px_50px_-26px_rgb(194_65_12/0.55)] shadow-glow transition hover:brightness-[1.06] active:translate-y-[0.5px] disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-400 disabled:text-slate-600 disabled:shadow-none disabled:active:translate-y-0 motion-reduce:hover:brightness-100 dark:disabled:from-slate-700 dark:disabled:to-slate-700 dark:disabled:text-slate-400 sm:min-h-[3.125rem] sm:w-auto sm:min-w-[14rem] sm:px-7"
        >
          {disabled && (
            <span className="inline-block size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
          )}
          {files.length > 1 ? t("upload.transcribeMany", { n: files.length }) : t("upload.transcribeOne")}
        </button>

        <aside
          role="note"
          aria-label={t("upload.asideAria")}
          className="w-full shrink-0 rounded-2xl border border-amber-200/85 bg-gradient-to-br from-amber-50 via-white/90 to-orange-50/50 p-3.5 text-start shadow-md shadow-amber-900/10 dark:border-amber-900/55 dark:from-amber-950/40 dark:via-slate-950/92 dark:to-amber-950/20 sm:max-w-md sm:p-4 lg:max-w-[22rem]"
        >
          <div className="flex gap-3 sm:gap-3.5">
            <span className="select-none shrink-0 text-xl leading-none text-amber-600 dark:text-amber-400" aria-hidden>
              ℹ️
            </span>
            <div className="min-w-0 space-y-2 text-[11.6px] leading-relaxed text-slate-700 dark:text-slate-300 sm:text-[12px]">
              <p className="font-semibold text-slate-900 dark:text-white">
                {t("upload.asideTitle", { engine: ENGINE_TRANSCRIPTION })}
              </p>
              <p>{t("upload.asideP1")}</p>
              <p>
                <Trans
                  i18nKey="upload.asideP2"
                  components={{ corr: <strong className="font-semibold text-slate-900 dark:text-slate-100" /> }}
                  values={{ course: ENGINE_COURSE }}
                />
              </p>
            </div>
          </div>
        </aside>
      </div>
      </div>
    </div>
    </div>
  );
}

export default memo(UploadZone);
