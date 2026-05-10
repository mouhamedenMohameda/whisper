import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { parseFlashcards } from "../utils/lessonParsers";

function Flashcard({
  flipped,
  question,
  answer,
  index,
  total,
  onToggle,
  t,
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="group relative h-44 w-full rounded-2xl outline-none focus-visible:ring-4 focus-visible:ring-brand-500 sm:h-52"
      style={{ perspective: "1100px" }}
    >
      <div
        className={`relative h-full w-full rounded-2xl transition-transform duration-700 [transform-style:preserve-3d] ${flipped ? "[transform:rotateY(180deg)]" : ""}`}
      >
        <span className="pointer-events-none absolute end-4 top-3 z-10 text-[11px] text-slate-400">
          {index + 1}/{total}
        </span>
        <div className="absolute inset-0 flex flex-col justify-center rounded-2xl border border-slate-200 bg-white p-5 text-start shadow-sm [backface-visibility:hidden] dark:border-slate-700 dark:bg-slate-900">
          <span className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-brand-600 dark:text-brand-400">
            {t("flashcards.question")}
          </span>
          <span className="text-start text-sm text-slate-900 dark:text-slate-100">{question}</span>
          <span className="mt-4 text-xs text-slate-400">{t("flashcards.flipSee")}</span>
        </div>
        <div className="absolute inset-0 flex flex-col justify-center rounded-2xl border border-brand-600 bg-gradient-to-br from-brand-900/90 via-slate-900 to-slate-950 p-5 text-start shadow-lg [transform:rotateY(180deg)] [backface-visibility:hidden]">
          <span className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-brand-200">
            {t("flashcards.answer")}
          </span>
          <span className="text-start text-sm text-white">{answer}</span>
          <span className="mt-4 text-xs text-white/70">{t("flashcards.flipBack")}</span>
        </div>
      </div>
    </button>
  );
}

export default function FlashcardDeck({ lessonMarkdown }) {
  const { t } = useTranslation();
  const cards = useMemo(() => parseFlashcards(lessonMarkdown), [lessonMarkdown]);
  const [flip, setFlip] = useState({});

  const toggle = (i) => {
    setFlip((f) => ({ ...f, [i]: !f[i] }));
  };

  if (cards.length === 0) {
    return (
      <div className="glass-panel rounded-2xl border border-dashed border-slate-300/80 p-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
        {t("flashcards.empty")}{" "}
        <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">Q: … / A: …</code>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-center text-sm text-slate-500 dark:text-slate-400">{t("flashcards.hint")}</p>
      <div className="mx-auto grid max-w-5xl gap-4 sm:grid-cols-2">
        {cards.map((c, i) => (
          <Flashcard
            key={i}
            flipped={!!flip[i]}
            question={c.q}
            answer={c.a}
            index={i}
            total={cards.length}
            onToggle={() => toggle(i)}
            t={t}
          />
        ))}
      </div>
      <div className="flex justify-center">
        <button
          type="button"
          onClick={() => setFlip({})}
          className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-100 dark:hover:bg-slate-800"
        >
          {t("flashcards.resetPack")}
        </button>
      </div>
    </div>
  );
}
