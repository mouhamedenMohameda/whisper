/**
 * Rendu lecture seule : rouge = autre langue ; accent chaud = passage très net à l’oral.
 *
 * @param {{ view?: { blocks?: Array<Record<string, unknown>> }; emptyHint?: string }} props
 */
export default function TranscriptMixedView({ view, emptyHint = "Pas de données pour cette vue." }) {
  const blocks = Array.isArray(view?.blocks) ? view.blocks : [];
  if (blocks.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-slate-200/90 p-6 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
        {emptyHint}
      </p>
    );
  }

  return (
    <div
      dir="auto"
      className="glass-panel max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-2xl p-4 font-sans text-sm leading-relaxed text-slate-800 dark:text-slate-100"
    >
      {blocks.map((b, idx) => {
        const kind = b?.kind ?? "text";
        if (kind === "file_header") {
          return (
            <span
              key={`fh-${idx}`}
              className="mb-1 mt-6 block border-b border-slate-200/80 pb-1 text-[0.8125rem] font-bold uppercase tracking-wide text-brand-700 first:mt-0 dark:border-slate-700 dark:text-brand-300"
            >
              {String(b?.text ?? "")}
            </span>
          );
        }
        if (kind === "marker") {
          return (
            <span key={`mk-${idx}`} className="text-slate-500 dark:text-slate-400">
              {String(b?.text ?? "")}
            </span>
          );
        }
        const translated = Boolean(b?.translated);
        const wr = b?.whisper_reliability;
        const wrObj = wr != null && typeof wr === "object" ? wr : null;
        const highRel = Boolean(wrObj?.high_reliability);
        const scoreN = typeof wrObj?.score_0_100 === "number" ? wrObj.score_0_100 : null;
        const dl = typeof b?.detected_lang === "string" ? b.detected_lang : "";
        const dr = typeof b?.detected_reason === "string" ? b.detected_reason : "";
        let tip;
        if (!translated) tip = undefined;
        else if (dr === "script_parasite") {
          tip = "Texte corrigé (caractères ou bruit parasites).";
        } else if (dl) {
          tip = `Traduit ou adapté depuis une autre langue (${dl}).`;
        } else tip = "Traduit ou adapté vers la langue du cours.";
        const relTip =
          highRel && wrObj
            ? scoreN != null
              ? `Passage très net à l’oral (indice ${Math.round(scoreN)}/100).`
              : "Passage très net à l’oral."
            : wrObj && !highRel
              ? "Relecture conseillée sur ce passage."
              : undefined;
        const mergedTip = [tip, relTip].filter(Boolean).join("\n\n") || undefined;
        const text = translated ? String(b?.display ?? b?.original ?? "") : String(b?.display ?? b?.original ?? "");
        const isHighConfidence = scoreN != null ? scoreN >= 95 : highRel;
        
        let clsColor = "text-slate-900 dark:text-slate-100";
        
        if (isHighConfidence) {
           // Sans diarisation, on applique une couleur unique (par exemple l'ambre/or ou le bleu) pour la haute confiance
           clsColor = "font-medium text-blue-600 dark:text-blue-400"; 
        } else if (!isHighConfidence) {
           // Basse confiance : on laisse en noir
           clsColor = "text-black font-medium underline decoration-dashed decoration-slate-400/70 dark:text-white cursor-help";
        }
        
        // Surcharge si c'est une traduction pure
        if (translated && !isHighConfidence) {
           clsColor = "font-bold text-red-600 dark:text-red-400";
        }

        return (
          <span key={`tx-${idx}`} title={mergedTip} className={clsColor}>
            {text}
          </span>
        );
      })}
    </div>
  );
}
