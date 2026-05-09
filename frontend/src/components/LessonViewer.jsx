import { isValidElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ExportButtons from "./ExportButtons.jsx";
import FlashcardDeck from "./FlashcardDeck.jsx";
import QuizModule from "./QuizModule.jsx";
import TranscriptMixedView from "./TranscriptMixedView.jsx";
import { extractNavHeadings, slugify } from "../utils/lessonParsers.js";
import { estimateTokensFromText, formatMru } from "../utils/usage.js";

function textFromChildren(children) {
  if (children == null || children === false) return "";
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }
  if (Array.isArray(children)) {
    return children.map(textFromChildren).join("");
  }
  if (isValidElement(children)) {
    return textFromChildren(children.props.children);
  }
  return "";
}

const mdComponents = {
  h2({ children }) {
    const label = textFromChildren(children).trim();
    const id = slugify(label);
    return (
      <h2
        id={id}
        className="scroll-mt-28 border-b border-slate-200 pb-2 font-display text-xl font-bold text-slate-900 dark:border-slate-800 dark:text-white"
      >
        {children}
      </h2>
    );
  },
  h3({ children }) {
    return (
      <h3 className="mt-6 text-lg font-semibold text-slate-900 dark:text-slate-50">
        {children}
      </h3>
    );
  },
  p({ children }) {
    return <p className="mb-4 text-slate-700 dark:text-slate-300">{children}</p>;
  },
  ul({ children }) {
    return <ul className="mb-4 list-disc space-y-2 pl-5 text-slate-700 dark:text-slate-300">{children}</ul>;
  },
  ol({ children }) {
    return (
      <ol className="mb-4 list-decimal space-y-2 pl-5 text-slate-700 dark:text-slate-300">{children}</ol>
    );
  },
  li({ children }) {
    return <li className="leading-relaxed">{children}</li>;
  },
  strong({ children }) {
    return <strong className="font-semibold text-brand-700 dark:text-brand-300">{children}</strong>;
  },
  hr() {
    return <hr className="my-8 border-slate-200 dark:border-slate-800" />;
  },
  blockquote({ children }) {
    return (
      <blockquote className="border-l-4 border-brand-500 pl-4 text-slate-600 italic dark:border-brand-400 dark:text-slate-400">
        {children}
      </blockquote>
    );
  },
};

export default function LessonViewer({
  activeTab,
  onTabChange,
  transcript,
  transcriptMixedView,
  lesson,
  subject,
  filename,
  language,
  speechChosenLabel,
  wordCount,
  durationMinutes,
  celebration,
  usage,
}) {
  const headings = extractNavHeadings(lesson || "");
  const u = usage || {};
  const transcriptTok = estimateTokensFromText(transcript);
  const totalMru =
    Number(u.whisperBilledMru ?? 0) + Number(u.claudeBilledMru ?? 0);
  const foreignN = Number(transcriptMixedView?.foreign_segment_count ?? 0) || 0;
  /** Blocs structurés disponibles ; violet seulement si le texte cours n’a pas divergé du plain unifié. */
  const unifiedPrimary = transcriptMixedView?.blocks?.length > 0;
  const violetInSync =
    unifiedPrimary &&
    typeof transcriptMixedView?.plain_text === "string" &&
    transcriptMixedView.plain_text === transcript;

  const jump = (id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const tabBtn = (id, label) => (
    <button
      type="button"
      key={id}
      onClick={() => onTabChange(id)}
      className={`shrink-0 rounded-full px-4 py-2 text-xs font-semibold transition sm:text-sm ${
        activeTab === id
          ? "bg-brand-600 text-white shadow-glow"
          : "bg-slate-100/90 text-slate-700 backdrop-blur hover:bg-slate-200 dark:bg-slate-800/90 dark:text-slate-200 dark:hover:bg-slate-700"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="space-y-6">
      <div
        className={`glass-panel flex flex-col gap-5 rounded-3xl border border-emerald-200/70 bg-gradient-to-br from-emerald-50/90 via-white to-teal-50/40 p-5 shadow-soft dark:border-emerald-900/40 dark:from-emerald-950/50 dark:via-slate-900/80 dark:to-teal-950/20 lg:flex-row lg:items-center lg:justify-between ${
          celebration ? "animate-celebrate" : ""
        }`}
      >
        <div className="min-w-0">
          <div className="font-display text-xl font-bold text-emerald-900 dark:text-emerald-100">
            Ton cours est prêt ! 🎉
          </div>
          <p className="mt-1 text-sm leading-relaxed text-emerald-900/85 dark:text-emerald-200/90">
            Navigue par onglet, saute aux sections dans la marge, exporte quand tout te convient.
          </p>
        </div>
        <ExportButtons lesson={lesson} subject={subject} filename={filename} disabled={false} />
      </div>

      <div className="glass-panel rounded-3xl p-5 text-sm shadow-soft">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Estimation cette session (MRU)
        </div>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500 dark:text-slate-400">Jetons transcript (est.)</dt>
            <dd className="font-semibold text-slate-900 dark:text-white">{transcriptTok}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500 dark:text-slate-400">Durée audio traitée</dt>
            <dd className="font-semibold text-slate-900 dark:text-white">
              {u.whisperAudioSeconds
                ? `${(u.whisperAudioSeconds / 60).toFixed(2)} min`
                : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500 dark:text-slate-400">Jetons cours (entrée · sortie)</dt>
            <dd className="font-semibold text-slate-900 dark:text-white">
              {(u.claudeInput ?? 0).toLocaleString("fr-FR")}
              {" · "}
              {(u.claudeOutput ?? 0).toLocaleString("fr-FR")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500 dark:text-slate-400">Total à prévoir (MRU)</dt>
            <dd className="text-lg font-bold text-brand-700 dark:text-brand-300">
              ~{formatMru(totalMru)} MRU
            </dd>
          </div>
        </dl>
        <dl className="mt-4 grid gap-3 border-t border-slate-100 pt-4 dark:border-slate-800 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-slate-500 dark:text-slate-400">Transcription</dt>
            <dd className="font-semibold text-slate-800 dark:text-slate-100">
              ~{formatMru(u.whisperBilledMru)} MRU
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500 dark:text-slate-400">Génération du cours</dt>
            <dd className="font-semibold text-slate-800 dark:text-slate-100">
              ~{formatMru(u.claudeBilledMru)} MRU
            </dd>
          </div>
        </dl>
      </div>

      <div className="-mx-1 flex gap-2 overflow-x-auto pb-1 pt-1">
        {tabBtn("transcript", "Transcript")}
        {tabBtn("lesson", "Cours complet")}
        {tabBtn("quiz", "Quiz")}
        {tabBtn("flashcards", "Fiches")}
      </div>

      <div className="flex flex-col gap-8 lg:flex-row">
        <div className="min-w-0 flex-1 space-y-4">
          {activeTab === "transcript" && (
            <>
              <div className="flex flex-wrap gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>
                  🌐 {speechChosenLabel ? `choix : ${speechChosenLabel} · Whisper : ` : ""}
                  {language || "—"}
                </span>
                <span className="text-slate-300 dark:text-slate-600">•</span>
                <span>📝 ~{wordCount} mots</span>
                <span className="text-slate-300 dark:text-slate-600">•</span>
                <span>🧮 ~{transcriptTok} jetons transcript (est.)</span>
                {durationMinutes > 0 && (
                  <>
                    <span className="text-slate-300 dark:text-slate-600">•</span>
                    <span>⏱ ~{durationMinutes} min</span>
                  </>
                )}
                <span className="text-slate-300 dark:text-slate-600">•</span>
                <span>💰 Total estimé&nbsp;: ~{formatMru(totalMru)} MRU</span>
                {unifiedPrimary && foreignN > 0 && violetInSync ? (
                  <>
                    <span className="text-slate-300 dark:text-slate-600">•</span>
                    <span className="text-violet-600 dark:text-violet-400">
                      🌍 {foreignN} passage{foreignN > 1 ? "s" : ""} autres langues (violet)
                    </span>
                  </>
                ) : unifiedPrimary && violetInSync ? (
                  <>
                    <span className="text-slate-300 dark:text-slate-600">•</span>
                    <span className="text-violet-700/90 dark:text-violet-400">Vue unifiée</span>
                  </>
                ) : unifiedPrimary && !violetInSync ? (
                  <>
                    <span className="text-slate-300 dark:text-slate-600">•</span>
                    <span className="text-amber-700 dark:text-amber-400">
                      Texte modifié — aperçu structuré masqué
                    </span>
                  </>
                ) : null}
              </div>
              {unifiedPrimary && violetInSync ? (
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Transcription alignée avec la langue du cours ; les extraits hors langue apparaissent en violet / gras.
                  Chronologie et repères [MM:SS] conservés.
                </p>
              ) : unifiedPrimary && !violetInSync ? (
                <p className="text-xs text-amber-800/90 dark:text-amber-300/90">
                  Le texte du cours a été modifié par rapport à la vue unifiée ; l’affichage structuré (violet) est
                  désactivé pour rester cohérent avec le texte brut.
                </p>
              ) : null}
              {unifiedPrimary && violetInSync ? (
                <TranscriptMixedView
                  view={transcriptMixedView}
                  emptyHint="Aucune donnée pour l’aperçu structuré — texte brut ci-dessous."
                />
              ) : (
                <pre className="glass-panel max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-2xl p-4 font-sans text-sm leading-relaxed text-slate-800 dark:text-slate-100">
                  {transcript}
                </pre>
              )}
            </>
          )}

          {activeTab === "lesson" && (
            <article className="glass-panel prose prose-slate max-w-none rounded-3xl p-5 dark:prose-invert prose-headings:scroll-mt-28 prose-headings:font-display prose-li:my-1 prose-table:border-collapse prose-table:text-sm prose-th:bg-slate-100 prose-th:px-3 prose-th:py-2 dark:prose-th:bg-slate-900 prose-td:border prose-td:border-slate-200 prose-td:px-3 prose-td:py-2 dark:prose-td:border-slate-800 sm:p-8">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                {lesson || ""}
              </ReactMarkdown>
            </article>
          )}

          {activeTab === "quiz" && <QuizModule lessonMarkdown={lesson} />}
          {activeTab === "flashcards" && <FlashcardDeck lessonMarkdown={lesson} />}
        </div>

        {activeTab === "lesson" && headings.length > 1 && (
          <aside className="w-full shrink-0 lg:w-56">
            <div className="glass-panel sticky top-28 rounded-2xl p-4 text-sm shadow-soft dark:!bg-slate-900/80">
              <div className="mb-3 font-display text-xs font-bold uppercase tracking-wider text-slate-900 dark:text-white">
                Dans ce cours
              </div>
              <nav className="space-y-1">
                {headings.map((h) => (
                  <button
                    key={h.id}
                    type="button"
                    onClick={() => jump(h.id)}
                    className="w-full truncate rounded-lg px-2 py-2 text-left text-xs text-brand-700 hover:bg-brand-50 dark:text-brand-300 dark:hover:bg-brand-950/60"
                  >
                    {h.title}
                  </button>
                ))}
              </nav>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
