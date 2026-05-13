import { useTranslation } from "react-i18next";
import { downloadExport, estimatePremiumPdf, downloadPremiumPdf } from "../utils/api";
import { useState } from "react";
import { createPortal } from "react-dom";

export default function ExportButtons({ lesson, subject, filename, language, disabled }) {
  const { t } = useTranslation();
  const [isExporting, setIsExporting] = useState(false);
  const [showPremiumModal, setShowPremiumModal] = useState(false);
  const [estimation, setEstimation] = useState(null);
  const base = filename || "lecture";

  const go = async (kind) => {
    try {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: kind === "pdf" ? t("exportBtn.genPdf") : t("exportBtn.genDocx"), type: "info" },
        }),
      );
      await downloadExport(
        kind,
        { lesson, subject: subject || "Lesson", filename: base, language: language || "fr" },
        `${base}_lesson.${kind === "pdf" ? "pdf" : "docx"}`
      );
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: t("exportBtn.downloadStarted"), type: "success" },
        }),
      );
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: e.message || t("exportBtn.exportFail"), type: "error" },
        }),
      );
    }
  };

  const startPremiumFlow = async () => {
    if (isExporting) return;
    try {
      setIsExporting(true);
      const reqBody = { lesson, subject: subject || "Lesson", filename: base, language: language || "fr" };
      const est = await estimatePremiumPdf(reqBody);
      setEstimation(est);
      setShowPremiumModal(true);
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: e.message || t("exportBtn.exportFail"), type: "error" },
        }),
      );
    } finally {
      setIsExporting(false);
    }
  };

  const confirmPremium = async () => {
    if (!estimation?.has_credits) return;
    setShowPremiumModal(false);
    try {
      setIsExporting(true);
      const reqBody = { lesson, subject: subject || "Lesson", filename: base, language: language || "fr" };
      
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { 
            msg: language?.startsWith("ar") ? "جاري إنشاء مستند احترافي..." : "Génération du document professionnel...", 
            type: "info" 
          },
        }),
      );

      await downloadPremiumPdf(reqBody, `${base}_pro.pdf`);

      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: t("exportBtn.downloadStarted"), type: "success" },
        }),
      );
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: e.message || t("exportBtn.exportFail"), type: "error" },
        }),
      );
    } finally {
      setIsExporting(false);
    }
  };

  const copyMd = async () => {
    try {
      await navigator.clipboard.writeText(lesson);
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: t("exportBtn.mdCopied"), type: "success" },
        }),
      );
    } catch {
      window.dispatchEvent(
        new CustomEvent("lecturai-toast", {
          detail: { msg: t("exportBtn.mdFail"), type: "error" },
        }),
      );
    }
  };

  const isAr = language?.startsWith("ar");

  return (
    <div className="flex flex-wrap gap-2">
      <button
        type="button"
        disabled={disabled || isExporting}
        onClick={() => go("pdf")}
        className="rounded-2xl border border-slate-200/90 bg-white/90 px-3.5 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-white disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
      >
        {t("exportBtn.pdf")}
      </button>
      
      <button
        type="button"
        disabled={disabled || isExporting}
        onClick={startPremiumFlow}
        className="relative overflow-hidden rounded-2xl border-2 border-brand-200 bg-white px-3.5 py-2 text-xs font-bold text-brand-700 shadow-md transition hover:scale-105 hover:bg-brand-50 disabled:opacity-50 dark:border-brand-900/50 dark:bg-slate-900/80 dark:text-brand-300"
      >
        <span className="relative z-10 flex items-center gap-1.5">
          <span className="text-sm">⭐</span>
          {isAr ? "تصدير احترافي PDF" : "Export PDF Élite"}
        </span>
      </button>

      <button
        type="button"
        disabled={disabled || isExporting}
        onClick={() => go("docx")}
        className="rounded-2xl border border-slate-200/90 bg-white/90 px-3.5 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-white disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
      >
        {t("exportBtn.word")}
      </button>
      
      <button
        type="button"
        disabled={disabled || isExporting}
        onClick={copyMd}
        className="rounded-2xl border border-slate-200/90 bg-white/90 px-3.5 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-white disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
      >
        {t("exportBtn.copyMd")}
      </button>

      {/* Modal Premium Compact via Portal */}
      {showPremiumModal && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-950/40 backdrop-blur-sm" onClick={() => setShowPremiumModal(false)} />
          <div 
            className="relative w-full max-w-[340px] max-h-[90vh] flex flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900 animate-in zoom-in-95 duration-200"
            dir={isAr ? "rtl" : "ltr"}
          >
            {/* Header Premium - Plus compact */}
            <div className="bg-brand-600 px-6 py-5 text-center text-white">
               <h3 className="mb-1 text-lg font-extrabold tracking-tight">
                 {isAr ? "تصدير PDF احترافي" : "Export PDF Élite"}
               </h3>
               <p className="text-[11px] font-medium opacity-90 leading-tight">
                 {isAr 
                   ? "حوّل دروسك إلى تحفة أكاديمية." 
                   : "Transformez votre cours en chef-d'œuvre académique."}
               </p>
            </div>
            
            <div className="flex-1 overflow-y-auto p-5">
              <div className="mb-4 flex flex-col items-center justify-center rounded-2xl bg-slate-50 py-5 dark:bg-slate-800/50">
                <span className="mb-1 text-[9px] font-bold uppercase tracking-[0.2em] text-slate-400">
                  {isAr ? "التكلفة التقديرية" : "Coût estimé"}
                </span>
                <span className="text-3xl font-black text-brand-600">
                  {estimation?.cost_mru ? estimation.cost_mru.toFixed(2) : "0.00"}
                  <span className="ml-1.5 text-xs font-bold text-brand-400">MRU</span>
                </span>
              </div>
              
              {!estimation?.has_credits && (
                <div className="mb-4 rounded-xl bg-red-50 p-3 text-[11px] font-semibold text-red-600 dark:bg-red-900/20 dark:text-red-400 text-center">
                  ⚠️ {isAr ? "رصيد غير كافٍ." : "Crédits insuffisants."}
                </div>
              )}

              <div className="flex gap-2">
                <button
                  onClick={() => setShowPremiumModal(false)}
                  className="flex-1 rounded-2xl border border-slate-200 py-2.5 text-sm font-bold text-slate-500 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400"
                >
                  {isAr ? "إلغاء" : "Annuler"}
                </button>
                <button
                  disabled={!estimation?.has_credits}
                  onClick={confirmPremium}
                  className="flex-[1.5] rounded-2xl bg-brand-600 py-2.5 text-sm font-bold text-white shadow-md shadow-brand-100 transition hover:bg-brand-700 hover:shadow-lg disabled:opacity-50 dark:shadow-none"
                >
                  {isAr ? "تأكيد" : "Confirmer"}
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
