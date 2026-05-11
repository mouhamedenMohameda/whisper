import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

/**
 * Masque par défaut les détails techniques (jetons, durées, MRU, etc.) ;
 * l’utilisateur les affiche au clic.
 *
 * @param {{ children: import("react").ReactNode; className?: string; compact?: boolean }} props
 */
export default function UsageDetailsToggle({ children, className = "", compact = false }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const baseId = useId();
  const panelId = `${baseId}-usage-panel`;
  const btnId = `${baseId}-usage-btn`;

  return (
    <div className={className}>
      <button
        id={btnId}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        title={compact ? t("usageDetails.show") : undefined}
        onClick={() => setOpen((o) => !o)}
        className="rounded-xl border border-slate-200/90 bg-white/85 px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-600 dark:bg-slate-900/75 dark:text-slate-200 dark:hover:bg-slate-800"
      >
        {open ? t("usageDetails.hide") : compact ? t("usageDetails.showShort") : t("usageDetails.show")}
      </button>
      {open ? (
        <div id={panelId} role="region" aria-labelledby={btnId} className="mt-3 space-y-3">
          {children}
        </div>
      ) : null}
    </div>
  );
}
