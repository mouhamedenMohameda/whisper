import { useTranslation } from "react-i18next";

export default function LanguageSwitcher({ className = "" }) {
  const { i18n, t } = useTranslation();
  const lng = i18n.language?.startsWith("ar") ? "ar" : "fr";

  return (
    <div
      className={`flex min-h-[2.75rem] shrink-0 items-stretch rounded-full border border-slate-200/90 bg-white/70 p-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-600 shadow-inner dark:border-slate-700 dark:bg-slate-900/75 dark:text-slate-300 sm:min-h-0 ${className}`.trim()}
      role="group"
      aria-label={t("langs.switchAria")}
    >
      <button
        type="button"
        onClick={() => void i18n.changeLanguage("fr")}
        className={`flex min-w-[2.75rem] flex-1 items-center justify-center rounded-full px-2.5 py-2 transition sm:min-w-0 sm:flex-none sm:px-3 sm:py-1 ${
          lng === "fr"
            ? "bg-brand-600 text-white shadow-sm dark:bg-brand-500"
            : "text-slate-600 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800"
        }`}
      >
        {t("langs.fr")}
      </button>
      <button
        type="button"
        onClick={() => void i18n.changeLanguage("ar")}
        dir="rtl"
        className={`flex min-w-[2.75rem] flex-1 items-center justify-center rounded-full px-2.5 py-2 transition sm:min-w-0 sm:flex-none sm:px-3 sm:py-1 ${
          lng === "ar"
            ? "bg-brand-600 text-white shadow-sm dark:bg-brand-500"
            : "text-slate-600 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800"
        }`}
      >
        {t("langs.ar")}
      </button>
    </div>
  );
}
