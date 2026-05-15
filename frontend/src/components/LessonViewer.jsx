import { isValidElement, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useTranslation } from "react-i18next";
import { ENGINE_TRANSCRIPTION } from "../branding.js";
import ExportButtons from "./ExportButtons.jsx";
import FlashcardDeck from "./FlashcardDeck.jsx";
import LessonShareButton from "./LessonShareButton.jsx";
import QuizModule from "./QuizModule.jsx";
import TranscriptMixedView from "./TranscriptMixedView.jsx";
import UsageDetailsToggle from "./UsageDetailsToggle.jsx";
import { allocateLessonBilledMru } from "../utils/allocateLessonBilledMru.js";
import { extractNavHeadings, slugify, stripQuizAndFlashcards, extractQuizMarkdownSection, extractFlashcardsMarkdownSection } from "../utils/lessonParsers.js";
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
    return textFromChildren(children.props?.children);
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
  jobPublicId,
}) {
  const { t } = useTranslation();
  const lessonMd = typeof lesson === "string" ? lesson : "";
  const displayLessonMd = useMemo(() => stripQuizAndFlashcards(lessonMd), [lessonMd]);
  const headings = extractNavHeadings(displayLessonMd);
  const u = usage || {};
  const transcriptTok = estimateTokensFromText(transcript);
  let splitLesson = { cours: 0, quiz: 0, fiches: 0 };
  let courseGenMru = 0;
  let insightOptMru = 0;
  let totalMru = 0;
  let mruTranscriptTab = 0;

  try {
    courseGenMru = Number(u.groqLessonBilledMru ?? u.claudeBilledMru ?? 0);
    insightOptMru = Number(u.groqInsightOptionalBilledMru ?? 0);
    totalMru = (Number(u.whisperBilledMru ?? 0) || 0) + courseGenMru + insightOptMru;
    splitLesson = allocateLessonBilledMru(lessonMd, courseGenMru);
    mruTranscriptTab = (Number(u.whisperBilledMru ?? 0) || 0) + insightOptMru;
  } catch (e) {
    console.error("LessonViewer allocation error", e);
  }

  const exportMd = useMemo(() => {
    if (activeTab === "quiz") return extractQuizMarkdownSection(lessonMd);
    if (activeTab === "flashcards") return extractFlashcardsMarkdownSection(lessonMd);
    if (activeTab === "transcript") return transcript || "";
    return displayLessonMd;
  }, [activeTab, lessonMd, displayLessonMd, transcript]);

  const mruCoursTab = splitLesson.cours;
  const mruQuizTab = splitLesson.quiz;
  const mruFichesTab = splitLesson.fiches;
  const billPoste = (titleKey, explainKey, mruVal) => (
    <div className="rounded-2xl border border-slate-200/90 bg-white/60 p-4 shadow-sm dark:border-slate-700 dark:bg-slate-950/50">
      <div className="font-display text-sm font-bold text-slate-900 dark:text-white">{t(titleKey)}</div>
      <div className="mt-1 text-lg font-bold tabular-nums text-brand-700 dark:text-brand-300">
        {t("lesson.billMruLine", { n: formatMru(mruVal) })}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{t(explainKey)}</p>
    </div>
  );
  const foreignN = Number(transcriptMixedView?.foreign_segment_count ?? 0) || 0;
  /** Blocs structurés disponibles ; surlignage chaud seulement si le texte cours n’a pas divergé du plain unifié. */
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
        className={`glass-panel relative z-20 flex flex-col gap-5 rounded-3xl border border-emerald-200/70 bg-gradient-to-br from-emerald-50/90 via-white to-teal-50/40 p-5 shadow-soft dark:border-emerald-900/40 dark:from-emerald-950/50 dark:via-slate-900/80 dark:to-teal-950/20 lg:flex-row lg:items-center lg:justify-between ${
          celebration ? "animate-celebrate" : ""
        }`}
      >
        <div className="min-w-0">
          <div className="font-display text-xl font-bold text-emerald-900 dark:text-emerald-100">{t("lesson.readyTitle")}</div>
          <p className="mt-1 text-sm leading-relaxed text-emerald-900/85 dark:text-emerald-200/90">{t("lesson.readySub")}</p>
        </div>
        <div className="relative flex flex-wrap items-center gap-2">
          <ExportButtons
            lesson={exportMd}
            subject={activeTab === "quiz" ? `${subject} - Quiz` : activeTab === "flashcards" ? `${subject} - Fiches` : subject}
            filename={activeTab === "quiz" ? `${filename}_quiz` : activeTab === "flashcards" ? `${filename}_fiches` : filename}
            language={language}
            disabled={false}
          />
          {activeTab === "lesson" && jobPublicId ? (
            <LessonShareButton jobPublicId={jobPublicId} subject={subject} />
          ) : null}
        </div>
      </div>

      <div className="glass-panel rounded-3xl p-5 text-sm shadow-soft">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{t("lesson.estimateTitle")}</div>

        <UsageDetailsToggle className="mt-2">
          <div className="rounded-2xl border border-slate-200/90 bg-gradient-to-br from-white to-slate-50/90 p-4 dark:border-slate-700 dark:from-slate-900/80 dark:to-slate-950/60">
            <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200/90 pb-3 dark:border-slate-700">
              <span className="text-xs font-bold uppercase tracking-wide text-slate-600 dark:text-slate-300">
                {t("lesson.billFourTotalLabel")}
              </span>
              <span className="font-display text-2xl font-bold tabular-nums text-brand-700 dark:text-brand-300">
                {t("lesson.billSessionTotal", { n: formatMru(totalMru) })}
              </span>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {billPoste("lesson.billTranscriptTitle", "lesson.billTranscriptExplain", mruTranscriptTab)}
              {billPoste("lesson.billCoursTitle", "lesson.billCoursExplain", mruCoursTab)}
              {billPoste("lesson.billQuizTitle", "lesson.billQuizExplain", mruQuizTab)}
              {billPoste("lesson.billFichesTitle", "lesson.billFichesExplain", mruFichesTab)}
            </div>
            <p className="mt-3 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{t("lesson.billSplitFootnote")}</p>
          </div>

          <div className="border-t border-slate-200/90 pt-4 dark:border-slate-700">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {t("lesson.estimateMeasuresHeading")}
            </div>
            <dl className="mt-2 grid gap-3 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-slate-500 dark:text-slate-400">{t("lesson.tokTrans")}</dt>
                <dd className="font-semibold text-slate-900 dark:text-white">{transcriptTok}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500 dark:text-slate-400">{t("lesson.audioDur")}</dt>
                <dd className="font-semibold text-slate-900 dark:text-white">
                  {u.whisperAudioSeconds
                    ? `${(u.whisperAudioSeconds / 60).toFixed(2)} min`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500 dark:text-slate-400">{t("lesson.courseTok")}</dt>
                <dd className="font-semibold text-slate-900 dark:text-white">
                  {(u.groqLessonInput ?? u.claudeInput ?? 0).toLocaleString("fr-FR")}
                  {" · "}
                  {(u.groqLessonOutput ?? u.claudeOutput ?? 0).toLocaleString("fr-FR")}
                </dd>
              </div>
            </dl>
          </div>
          <p className="text-xs leading-relaxed text-slate-500 dark:text-slate-400">{t("lesson.estimateFootnote")}</p>
        </UsageDetailsToggle>
      </div>

      <div className="-mx-1 flex gap-2 overflow-x-auto pb-1 pt-1">
        {tabBtn("transcript", t("lesson.tabTranscript"))}
        {tabBtn("lesson", t("lesson.tabLesson"))}
        {tabBtn("quiz", t("lesson.tabQuiz"))}
        {tabBtn("flashcards", t("lesson.tabFlash"))}
      </div>

      <div className="flex flex-col gap-8 lg:flex-row">
        <div className="min-w-0 flex-1 space-y-4">
          {activeTab === "transcript" && (
            <>
              <div className="flex flex-wrap gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>
                  🌐{" "}
                  {speechChosenLabel
                    ? t("lesson.choiceIntro", { speech: speechChosenLabel, transcription: ENGINE_TRANSCRIPTION })
                    : ""}
                  {language || t("common.dash")}
                </span>
                <span className="text-slate-300 dark:text-slate-600">•</span>
                <span>📝 {t("lesson.words", { n: wordCount })}</span>
                {unifiedPrimary && foreignN > 0 && violetInSync ? (
                  <>
                    <span className="text-slate-300 dark:text-slate-600">•</span>
                    <span className="text-brand-600 dark:text-brand-400">
                      {foreignN === 1
                        ? t("lesson.passageOtherSingular", { count: foreignN })
                        : t("lesson.passageOtherPlural", { count: foreignN })}
                    </span>
                  </>
                ) : unifiedPrimary && violetInSync ? (
                  <>
                    <span className="text-slate-300 dark:text-slate-600">•</span>
                    <span className="text-brand-700/90 dark:text-brand-400">{t("lesson.unifiedBadge")}</span>
                  </>
                ) : unifiedPrimary && !violetInSync ? (
                  <>
                    <span className="text-slate-300 dark:text-slate-600">•</span>
                    <span className="text-amber-700 dark:text-amber-400">{t("lesson.textEdited")}</span>
                  </>
                ) : null}
              </div>
              <UsageDetailsToggle compact className="mt-2">
                <div className="flex flex-wrap gap-2 text-xs text-slate-500 dark:text-slate-400">
                  <span>🧮 {t("lesson.tokTransLine", { n: transcriptTok })}</span>
                  {durationMinutes > 0 ? (
                    <>
                      <span className="text-slate-300 dark:text-slate-600">•</span>
                      <span>⏱ {t("lesson.min", { n: durationMinutes })}</span>
                    </>
                  ) : null}
                  <span className="text-slate-300 dark:text-slate-600">•</span>
                  <span>💰 {t("lesson.totalEst", { n: formatMru(totalMru) })}</span>
                </div>
              </UsageDetailsToggle>
              {unifiedPrimary && violetInSync ? (
                <p className="text-xs text-slate-500 dark:text-slate-400">{t("lesson.alignHint")}</p>
              ) : unifiedPrimary && !violetInSync ? (
                <p className="text-xs text-amber-800/90 dark:text-amber-300/90">{t("lesson.divergedHint")}</p>
              ) : null}
              {unifiedPrimary && violetInSync ? (
                <TranscriptMixedView
                  view={transcriptMixedView}
                  emptyHint={t("lesson.emptyStructured")}
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
                {displayLessonMd}
              </ReactMarkdown>
            </article>
          )}

          {activeTab === "quiz" && <QuizModule lessonMarkdown={lessonMd} />}
          {activeTab === "flashcards" && <FlashcardDeck lessonMarkdown={lessonMd} />}
        </div>

        {activeTab === "lesson" && headings.length > 1 && (
          <aside className="w-full shrink-0 lg:w-56">
            <div className="glass-panel sticky top-28 rounded-2xl p-4 text-sm shadow-soft dark:!bg-slate-900/80">
              <div className="mb-3 font-display text-xs font-bold uppercase tracking-wider text-slate-900 dark:text-white">
                {t("lesson.navTitle")}
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
