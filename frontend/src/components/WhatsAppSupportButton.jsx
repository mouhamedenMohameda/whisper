import { SUPPORT_WHATSAPP_HREF } from "../branding.js";

/**
 * @param {{ variant?: "header" | "auth" | "admin"; className?: string }} props
 */
export default function WhatsAppSupportButton({ variant = "header", className = "" }) {
  const styles =
    variant === "auth"
      ? "inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-emerald-300/70 bg-emerald-500/10 px-4 py-3 text-sm font-semibold text-emerald-900 shadow-sm transition hover:bg-emerald-500/15 dark:border-emerald-800/70 dark:bg-emerald-950/40 dark:text-emerald-100 dark:hover:bg-emerald-950/60"
      : variant === "admin"
        ? "inline-flex items-center gap-1.5 rounded-full border border-emerald-300/70 bg-emerald-500/10 px-3.5 py-2 text-[11px] font-semibold text-emerald-900 shadow-sm transition hover:bg-emerald-500/15 dark:border-emerald-900/60 dark:text-emerald-200 dark:hover:bg-emerald-950/50"
        : "inline-flex items-center gap-1.5 rounded-full border border-emerald-200/90 bg-white/75 px-3 py-2 text-[11px] font-bold text-emerald-900 shadow-sm backdrop-blur-md transition hover:border-emerald-300 hover:bg-white dark:border-emerald-900/55 dark:bg-emerald-950/35 dark:text-emerald-100 dark:hover:bg-emerald-950/55 sm:px-4";

  return (
    <a
      href={SUPPORT_WHATSAPP_HREF}
      target="_blank"
      rel="noopener noreferrer"
      className={`${styles} ${className}`.trim()}
    >
      <span aria-hidden>💬</span>
      <span>Support</span>
      <span className="sr-only"> — contacter l&apos;admin sur WhatsApp (nouvel onglet)</span>
    </a>
  );
}
