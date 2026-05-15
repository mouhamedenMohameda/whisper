import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useTranslation } from "react-i18next";
import { fetchPublicLesson } from "../utils/api.js";

const TOKEN_RE = /^\/c\/([A-Za-z0-9_-]{16,128})\/?$/;

/** Extrait le token depuis l'URL courante (`/c/<token>`) ou retourne null si pas une route publique. */
export function publicShareTokenFromLocation() {
  try {
    const m = TOKEN_RE.exec(window.location.pathname || "");
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

const mdComponents = {
  h1: ({ children }) => (
    <h1 className="mb-4 font-display text-3xl font-extrabold text-slate-900 dark:text-white">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-3 mt-8 border-b border-slate-200 pb-2 font-display text-xl font-bold text-slate-900 dark:border-slate-800 dark:text-white">
      {children}
    </h2>
  ),
  h3: ({ children }) => <h3 className="mt-6 text-lg font-semibold text-slate-900 dark:text-slate-50">{children}</h3>,
  p: ({ children }) => <p className="mb-4 text-slate-700 dark:text-slate-300">{children}</p>,
  ul: ({ children }) => <ul className="mb-4 list-disc space-y-1 pl-5 text-slate-700 dark:text-slate-300">{children}</ul>,
  ol: ({ children }) => <ol className="mb-4 list-decimal space-y-1 pl-5 text-slate-700 dark:text-slate-300">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-brand-700 dark:text-brand-300">{children}</strong>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-brand-500 pl-4 italic text-slate-600 dark:border-brand-400 dark:text-slate-400">
      {children}
    </blockquote>
  ),
};

export default function PublicLessonView({ token }) {
  const { t } = useTranslation();
  const [state, setState] = useState({ loading: true, error: "", data: null });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: "", data: null });
    fetchPublicLesson(token)
      .then((data) => {
        if (cancelled) return;
        setState({ loading: false, error: "", data });
        try {
          document.title = `LecturAI — ${data.subject || "Cours partagé"}`;
        } catch {
          /* noop */
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setState({ loading: false, error: err?.message || "Lien invalide.", data: null });
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const ctaHref = (() => {
    const ref = state.data?.referrer_code;
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return ref ? `${origin}/?ref=${encodeURIComponent(ref)}` : `${origin}/`;
  })();

  if (state.loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="size-12 animate-spin rounded-full border-4 border-brand-600/25 border-t-brand-600" />
      </div>
    );
  }

  if (state.error || !state.data) {
    return (
      <div className="mx-auto max-w-xl px-6 py-24 text-center">
        <h1 className="font-display text-2xl font-bold text-slate-900 dark:text-white">
          {t("publicShare.notFoundTitle", "Lien indisponible")}
        </h1>
        <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
          {state.error || t("publicShare.notFoundBody", "Cette leçon n'existe pas ou n'est plus partagée.")}
        </p>
        <a
          href="/"
          className="mt-8 inline-block rounded-full bg-gradient-to-r from-brand-600 to-amber-500 px-6 py-3 text-sm font-bold text-white shadow-glow transition hover:brightness-105"
        >
          {t("publicShare.ctaTryFree", "Génère ton cours gratuitement")}
        </a>
      </div>
    );
  }

  const { lesson_markdown, subject, views } = state.data;

  return (
    <div className="relative min-h-screen pb-32">
      <header className="border-b border-slate-200/80 bg-white/80 backdrop-blur dark:border-slate-800/80 dark:bg-slate-900/70">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4 sm:px-6">
          <a href="/" className="flex items-baseline gap-2">
            <span className="font-display text-2xl font-extrabold tracking-tight bg-gradient-to-r from-brand-600 via-amber-500 to-rose-500 bg-clip-text text-transparent">
              LecturAI
            </span>
            <span className="rounded-full bg-brand-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-brand-700 dark:bg-brand-900/40 dark:text-brand-300">
              {t("publicShare.publicBadge", "partagé")}
            </span>
          </a>
          <a
            href={ctaHref}
            className="rounded-full bg-gradient-to-r from-brand-600 to-amber-500 px-4 py-2 text-xs font-bold text-white shadow-glow transition hover:brightness-105 sm:text-sm"
          >
            {t("publicShare.ctaHeader", "Génère le tien")}
          </a>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <div className="mb-6 flex flex-wrap items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
          <span className="rounded-full bg-slate-100 px-3 py-1 font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-200">
            📚 {subject || "General"}
          </span>
          {Number(views) > 0 ? (
            <span>
              👁 {views} {t("publicShare.viewsSuffix", "vues")}
            </span>
          ) : null}
        </div>

        <article className="prose prose-slate max-w-none rounded-3xl bg-white/80 p-6 shadow-soft dark:prose-invert dark:bg-slate-900/60 sm:p-10">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
            {lesson_markdown || ""}
          </ReactMarkdown>
        </article>
      </main>

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 px-4 py-4 shadow-soft-lg backdrop-blur dark:border-slate-800 dark:bg-slate-900/95 sm:px-6">
        <div className="mx-auto flex max-w-3xl flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="font-display text-sm font-bold text-slate-900 dark:text-white">
              {t("publicShare.bottomCtaTitle", "Transforme ton enregistrement en cours en 5 min")}
            </div>
            <div className="text-xs text-slate-600 dark:text-slate-400">
              {t("publicShare.bottomCtaSub", "Transcription + cours structuré + quiz · français & arabe.")}
            </div>
          </div>
          <a
            href={ctaHref}
            className="shrink-0 rounded-full bg-gradient-to-r from-brand-600 via-amber-500 to-rose-500 px-5 py-3 text-sm font-bold text-white shadow-glow transition hover:brightness-105"
          >
            {t("publicShare.ctaBottom", "Essayer gratuitement →")}
          </a>
        </div>
      </div>
    </div>
  );
}
