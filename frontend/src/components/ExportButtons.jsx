import { downloadExport } from "../utils/api";

export default function ExportButtons({ lesson, subject, filename, disabled }) {
  const base = filename || "lecture";

  const go = async (kind) => {
    try {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: kind === "pdf" ? "Génération du PDF…" : "Génération du Word…", type: "info" },
        }),
      );
      await downloadExport(
        kind,
        { lesson, subject: subject || "Lesson", filename: base },
        `${base}_lesson.${kind === "pdf" ? "pdf" : "docx"}`,
      );
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: "Téléchargement lancé.", type: "success" },
        }),
      );
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: e.message || "Échec de l&apos;export.", type: "error" },
        }),
      );
    }
  };

  const copyMd = async () => {
    try {
      await navigator.clipboard.writeText(lesson);
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: "Markdown copié dans le presse-papiers.", type: "success" },
        }),
      );
    } catch {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: "Impossible de copier le markdown.", type: "error" },
        }),
      );
    }
  };

  return (
    <div className="flex flex-wrap gap-2">
      <button
        type="button"
        disabled={disabled}
        onClick={() => go("pdf")}
        className="rounded-2xl border border-slate-200/90 bg-white/90 px-3.5 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-white disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
      >
        📄 Exporter PDF
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={() => go("docx")}
        className="rounded-2xl border border-slate-200/90 bg-white/90 px-3.5 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-white disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
      >
        📝 Exporter Word
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={copyMd}
        className="rounded-2xl border border-slate-200/90 bg-white/90 px-3.5 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-white disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
      >
        📋 Copier le Markdown
      </button>
    </div>
  );
}
