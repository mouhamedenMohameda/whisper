import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { parseQuiz } from "../utils/lessonParsers";

export default function QuizModule({ lessonMarkdown }) {
  const { t } = useTranslation();
  const quiz = useMemo(() => parseQuiz(lessonMarkdown), [lessonMarkdown]);
  const [answers, setAnswers] = useState({});
  const [revealed, setRevealed] = useState({});

  const pick = (qi, choice) => {
    if (revealed[qi]) return;
    setAnswers((a) => ({ ...a, [qi]: choice }));
    setRevealed((r) => ({ ...r, [qi]: true }));
  };

  const correctCount = useMemo(() => {
    return Object.entries(answers).filter(
      ([qi, ai]) => quiz[qi] && quiz[qi].correct === ai,
    ).length;
  }, [answers, quiz]);

  const allDone = quiz.length > 0 && quiz.every((_, qi) => revealed[qi]);

  const reset = () => {
    setAnswers({});
    setRevealed({});
  };

  if (quiz.length === 0) {
    return (
      <div className="glass-panel rounded-2xl border border-dashed border-slate-300/80 p-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
        {t("quiz.empty")}
      </div>
    );
  }

  if (allDone) {
    return (
      <div className="glass-panel rounded-3xl p-10 text-center shadow-soft dark:!bg-slate-900/60">
        <div className="font-display text-5xl font-extrabold text-brand-600 dark:text-brand-400">
          {correctCount}/{quiz.length}
        </div>
        <p className="mt-4 text-sm text-slate-600 dark:text-slate-300">
          {correctCount === quiz.length ? t("quiz.scoreSubPerfect") : t("quiz.scoreSubReview")}
        </p>
        <button
          type="button"
          onClick={reset}
          className="mt-6 rounded-2xl border border-slate-200/90 bg-white/70 px-4 py-2 text-sm font-semibold text-slate-800 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-100 dark:hover:bg-slate-800"
        >
          {t("quiz.restart")}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {quiz.map((q, qi) => {
        const chosen = answers[qi];
        const show = !!revealed[qi];
        const nextIdx = quiz.findIndex((_, j) => !revealed[j]);
        const locked = qi > nextIdx && nextIdx !== -1;

        return (
          <div
            key={qi}
            className={`glass-panel rounded-2xl border border-slate-200/90 p-5 dark:!bg-slate-900/50 ${locked ? "opacity-40" : ""}`}
          >
            <div className="text-sm font-semibold text-slate-900 dark:text-white">
              {`${t("lesson.tabQuiz")} ${qi + 1}. ${q.question}`}
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {q.options.map((opt, oi) => {
                let cls =
                  "rounded-xl border border-slate-200 px-4 py-3 text-start text-sm text-slate-800 hover:border-brand-500 dark:border-slate-700 dark:text-slate-100 dark:hover:border-brand-500";
                if (show) {
                  if (oi === q.correct)
                    cls =
                      "rounded-xl border border-emerald-500 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-800 dark:text-emerald-200";
                  else if (oi === chosen && oi !== q.correct)
                    cls =
                      "rounded-xl border border-rose-500 bg-rose-500/10 px-4 py-3 text-sm text-rose-800 dark:text-rose-200";
                }
                return (
                  <button
                    key={oi}
                    type="button"
                    disabled={locked}
                    onClick={() => pick(qi, oi)}
                    className={cls}
                  >
                    {opt}
                  </button>
                );
              })}
            </div>
            {show && q.explanation && (
              <p className="mt-4 rounded-xl bg-slate-50 p-4 text-xs text-slate-600 dark:bg-slate-950 dark:text-slate-300">
                💡 {q.explanation}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
