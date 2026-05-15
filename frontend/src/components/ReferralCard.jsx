import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchMyReferralInfo } from "../utils/api.js";
import { buildReferralShareUrl } from "../utils/referral.js";

function toast(msg, type = "info") {
  window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg, type } }));
}

function formatMru(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  return v.toLocaleString("fr-FR", { maximumFractionDigits: 2 });
}

export default function ReferralCard() {
  const { t } = useTranslation();
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchMyReferralInfo()
      .then((d) => {
        if (!cancelled) setInfo(d);
      })
      .catch(() => {
        if (!cancelled) setInfo(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="rounded-3xl border border-slate-200/80 bg-white/60 p-6 dark:border-slate-800/80 dark:bg-slate-900/40">
        <div className="h-4 w-40 animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
        <div className="mt-4 h-12 animate-pulse rounded-xl bg-slate-200 dark:bg-slate-700" />
      </div>
    );
  }

  if (!info?.referral_code) return null;

  const code = info.referral_code;
  const shareUrl = info.share_url || buildReferralShareUrl(code);
  const bonusSignup = formatMru(info.bonus_signup_mru);
  const bonusPaidRef = formatMru(info.bonus_paid_mru_referrer);
  const bonusPaidNew = formatMru(info.bonus_paid_mru_referred);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl || code);
      toast(t("referral.copied", "Lien copié — partage-le où tu veux !"), "success");
    } catch {
      toast(t("referral.copyFail", "Copie impossible — sélectionne et copie manuellement."), "error");
    }
  };

  const waMessage = encodeURIComponent(
    t("referral.waMessage", {
      url: shareUrl || "",
      bonus: bonusSignup,
      defaultValue: "Salut ! J'utilise LecturAI pour transformer mes cours enregistrés en fiches structurées. Inscris-toi avec mon lien et on gagne {{bonus}} MRU chacun : {{url}}",
    }),
  );

  return (
    <section className="relative overflow-hidden rounded-3xl border border-emerald-200/60 bg-gradient-to-br from-emerald-50/95 via-white to-teal-50/40 p-6 shadow-soft dark:border-emerald-900/50 dark:from-emerald-950/40 dark:via-slate-900/70 dark:to-teal-950/20">
      <div className="flex items-start gap-3">
        <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-600 text-2xl text-white shadow-lg">
          🎁
        </span>
        <div className="min-w-0">
          <h2 className="font-display text-lg font-bold text-slate-900 dark:text-white">
            {t("referral.title", "Invite tes amis, gagnez ensemble")}
          </h2>
          <p className="mt-1 text-sm leading-relaxed text-slate-600 dark:text-slate-300">
            {t("referral.subtitle", {
              bonusSignup,
              bonusPaidRef,
              bonusPaidNew,
              defaultValue:
                "Quand un ami crée son compte avec ton lien, vous recevez {{bonusSignup}} MRU chacun. À sa 1ère recharge validée : tu gagnes {{bonusPaidRef}} MRU de plus, et lui {{bonusPaidNew}} MRU.",
            })}
          </p>
        </div>
      </div>

      <div className="mt-5 rounded-2xl border border-slate-200/90 bg-white/80 p-4 dark:border-slate-700 dark:bg-slate-950/40">
        <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-400">
          {t("referral.codeLabel", "Ton code")}
        </div>
        <div className="mt-1 font-mono text-2xl font-extrabold tracking-widest text-emerald-700 dark:text-emerald-300">
          {code}
        </div>
        {shareUrl ? (
          <div className="mt-3">
            <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-400">
              {t("referral.urlLabel", "Lien de parrainage")}
            </div>
            <div className="mt-1 truncate rounded-xl bg-slate-100 px-3 py-2 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-200">
              {shareUrl}
            </div>
          </div>
        ) : null}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={copy}
          className="rounded-full bg-emerald-600 px-4 py-2 text-xs font-bold text-white shadow-md transition hover:bg-emerald-700"
        >
          {t("referral.copyBtn", "Copier le lien")}
        </button>
        <a
          href={`https://wa.me/?text=${waMessage}`}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-full bg-[#25D366] px-4 py-2 text-xs font-bold text-white shadow-md transition hover:brightness-110"
        >
          {t("referral.shareWa", "Partager sur WhatsApp")}
        </a>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 text-center">
        <div className="rounded-xl bg-white/70 px-3 py-2 dark:bg-slate-900/60">
          <div className="text-[10px] font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("referral.referredCount", "Filleuls")}
          </div>
          <div className="font-display text-xl font-bold text-slate-900 dark:text-white">
            {info.referred_count ?? 0}
          </div>
        </div>
        <div className="rounded-xl bg-white/70 px-3 py-2 dark:bg-slate-900/60">
          <div className="text-[10px] font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("referral.paidReferredCount", "Rechargés")}
          </div>
          <div className="font-display text-xl font-bold text-slate-900 dark:text-white">
            {info.paid_referred_count ?? 0}
          </div>
        </div>
      </div>
    </section>
  );
}
