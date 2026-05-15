import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { disableLessonShare, enableLessonShare, fetchLessonShareStatus } from "../utils/api.js";

function toast(msg, type = "info") {
  window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg, type } }));
}

function buildClientShareUrl(token) {
  if (!token) return null;
  try {
    return `${window.location.origin}/c/${token}`;
  } catch {
    return null;
  }
}

/**
 * Bouton compact pour activer / désactiver le partage public d'une leçon.
 * Affiche l'URL publique et un bouton WhatsApp pour partage rapide.
 *
 * Props : { jobPublicId, subject } — masqué tant que jobPublicId n'est pas fourni
 * (cas batch multi-fichiers où le partage est ambigu).
 */
export default function LessonShareButton({ jobPublicId, subject }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState({ loading: true, enabled: false, url: null, views: 0 });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!jobPublicId) return undefined;
    let cancelled = false;
    setStatus({ loading: true, enabled: false, url: null, views: 0 });
    fetchLessonShareStatus(jobPublicId)
      .then((s) => {
        if (cancelled) return;
        setStatus({
          loading: false,
          enabled: Boolean(s.enabled),
          url: s.url || (s.token ? buildClientShareUrl(s.token) : null),
          views: Number(s.views || 0),
        });
      })
      .catch(() => {
        if (cancelled) return;
        setStatus({ loading: false, enabled: false, url: null, views: 0 });
      });
    return () => {
      cancelled = true;
    };
  }, [jobPublicId]);

  if (!jobPublicId) return null;

  const activate = async () => {
    setBusy(true);
    try {
      const r = await enableLessonShare(jobPublicId);
      const url = r.url || buildClientShareUrl(r.token);
      setStatus({ loading: false, enabled: true, url, views: Number(r.views || 0) });
      setOpen(true);
      toast(t("lessonShare.enabled", "Partage activé — copie le lien ci-dessous."), "success");
    } catch (err) {
      toast(err?.message || t("lessonShare.failed", "Activation impossible."), "error");
    } finally {
      setBusy(false);
    }
  };

  const deactivate = async () => {
    setBusy(true);
    try {
      await disableLessonShare(jobPublicId);
      setStatus({ loading: false, enabled: false, url: null, views: 0 });
      toast(t("lessonShare.disabled", "Lien public désactivé."), "success");
    } catch (err) {
      toast(err?.message || t("lessonShare.failed", "Désactivation impossible."), "error");
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!status.url) return;
    try {
      await navigator.clipboard.writeText(status.url);
      toast(t("lessonShare.copied", "Lien copié."), "success");
    } catch {
      toast(t("lessonShare.copyFail", "Copie impossible — sélectionne et copie manuellement."), "error");
    }
  };

  if (status.loading) {
    return (
      <button
        type="button"
        disabled
        className="rounded-full border border-slate-200 bg-white/70 px-3 py-1.5 text-xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-900/60"
      >
        …
      </button>
    );
  }

  if (!status.enabled) {
    return (
      <button
        type="button"
        onClick={activate}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-full border border-emerald-300/70 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-900 transition hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-800/60 dark:bg-emerald-950/40 dark:text-emerald-100 dark:hover:bg-emerald-950/60"
      >
        🔗 {t("lessonShare.enableBtn", "Partager publiquement")}
      </button>
    );
  }

  const waText = encodeURIComponent(
    t("lessonShare.waMessage", {
      subject: subject || "cours",
      url: status.url || "",
      defaultValue: "Voici un cours « {{subject}} » généré par LecturAI. Tu peux le lire ici : {{url}}",
    }),
  );

  return (
    <div className="relative z-50 inline-flex items-center gap-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-full bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white shadow-sm transition hover:bg-emerald-700"
      >
        🔗 {t("lessonShare.publicBadge", "Partagé")} · {status.views} 👁
      </button>
      {open && status.url ? (
        <>
          <button
            type="button"
            aria-label={t("lessonShare.closeBtn", "Fermer")}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-[55] bg-slate-900/30 backdrop-blur-[1px] sm:hidden"
          />
          <div
            className="fixed inset-x-3 bottom-3 z-[60] max-h-[80vh] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-4 text-xs shadow-soft-lg dark:border-slate-700 dark:bg-slate-900 sm:absolute sm:inset-auto sm:end-0 sm:top-full sm:mt-2 sm:w-[22rem] sm:max-w-[calc(100vw-1.5rem)]"
            role="dialog"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="font-semibold text-slate-700 dark:text-slate-200">
                {t("lessonShare.linkLabel", "Lien public")}
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label={t("lessonShare.closeBtn", "Fermer")}
                className="rounded-full p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200 sm:hidden"
              >
                ✕
              </button>
            </div>
            <div className="mt-2 break-all rounded-xl bg-slate-100 px-3 py-2 text-[11px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">
              {status.url}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={copy}
                className="rounded-full bg-slate-800 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-slate-700 dark:bg-slate-200 dark:text-slate-900 dark:hover:bg-white"
              >
                {t("lessonShare.copyBtn", "Copier")}
              </button>
              <a
                href={`https://wa.me/?text=${waText}`}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-full bg-[#25D366] px-3 py-1.5 text-[11px] font-bold text-white transition hover:brightness-110"
              >
                WhatsApp
              </a>
              <button
                type="button"
                onClick={deactivate}
                disabled={busy}
                className="rounded-full border border-rose-200 px-3 py-1.5 text-[11px] font-bold text-rose-700 transition hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800/60 dark:text-rose-300 dark:hover:bg-rose-950/40"
              >
                {t("lessonShare.disableBtn", "Désactiver")}
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
