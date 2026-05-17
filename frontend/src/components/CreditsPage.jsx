import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ENGINE_COURSE, ENGINE_TRANSCRIPTION } from "../branding.js";
import { apiUrl, getAuthHeaders, parseJsonResponse } from "../utils/api.js";
import { appLocaleTag } from "../utils/locale.js";
import ReferralCard from "./ReferralCard.jsx";
import TelegramLinkCard from "./TelegramLinkCard.jsx";

function toast(msg, type) {
  window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg, type } }));
}

/** @typedef {undefined | null | Record<string, unknown>} MeShape */

/** Inline icon to avoid new dependencies */
function IconWallet(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden {...props}>
      <path d="M19 7V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1" />
      <path d="M16 12h4a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v2a1 1 0 0 0 1 1Z" />
      <circle cx="17.5" cy="10.5" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

function IconUpload(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden {...props}>
      <path d="M12 4v12m0 0 3.5-3.5M12 16l-3.5-3.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5 19h14" strokeLinecap="round" />
    </svg>
  );
}

function IconCopy(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden {...props}>
      <rect x="8.5" y="8.5" width="11" height="11" rx="2" />
      <path d="M5.5 15.5v-8a2 2 0 0 1 2-2h8" />
    </svg>
  );
}

export default function CreditsPage({ onBack, onWalletUpdated }) {
  const { t, i18n } = useTranslation();
  const [loading, setLoading] = useState(true);
  /** @type {[MeShape, (v: MeShape) => void]} */
  const [me, setMe] = useState(undefined);
  const [requests, setRequests] = useState([]);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const fileRef = useRef(null);

  const statusLabels = useMemo(
    () => ({
      pending: t("creditsPage.statusPending"),
      approved: t("creditsPage.statusApproved"),
      rejected: t("creditsPage.statusRejected"),
    }),
    [t],
  );

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const rm = await fetch(apiUrl("/api/credits/me"), { headers: getAuthHeaders(false) });
      const pm = await parseJsonResponse(rm);
      if (!pm.ok) throw new Error(pm.errorMessage || t("creditsPage.loadError"));
      setMe(pm.data);

      const rr = await fetch(apiUrl("/api/credits/topup-requests/mine"), { headers: getAuthHeaders(false) });
      const pr = await parseJsonResponse(rr);
      if (pr.ok) setRequests(pr.data.requests || []);
      else setRequests([]);
    } catch (e) {
      toast(e?.message || t("admin.toast.loadError"), "error");
      setMe(null);
      setRequests([]);
    } finally {
      setLoading(false);
      onWalletUpdated?.();
    }
  }, [onWalletUpdated, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const copyNumber = useCallback(
    async (num) => {
      try {
        await navigator.clipboard.writeText(num);
        toast(t("creditsPage.numberCopied"), "success");
      } catch {
        toast(t("creditsPage.copyFailed"), "error");
      }
    },
    [t],
  );

  const setFileFromList = useCallback((files) => {
    const f = files?.[0];
    setFile(f && f.size > 0 ? f : null);
  }, []);

  async function submitProof(e) {
    e.preventDefault();
    if (!file) {
      toast(t("creditsPage.chooseProof"), "error");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(apiUrl("/api/credits/topup-requests"), {
        method: "POST",
        headers: getAuthHeaders(false),
        body: fd,
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage);
      toast(typeof p.data?.message === "string" ? p.data.message : t("creditsPage.sent"), "success");
      setFile(null);
      reload();
    } catch (err) {
      toast(err.message || String(err), "error");
    } finally {
      setBusy(false);
    }
  }

  if (me === undefined && loading) {
    return (
      <div className="relative mx-auto max-w-2xl px-1">
        <div
          className="motion-reduce-hide mx-auto flex min-h-[12rem] max-w-md flex-col items-center justify-center gap-3 rounded-3xl border border-brand-200/60 bg-gradient-to-br from-white via-brand-50/80 to-amber-50/55 px-6 py-10 text-center shadow-soft dark:border-brand-900/40 dark:from-slate-900 dark:via-brand-950/35 dark:to-amber-950/25"
          aria-busy
        >
          <div className="h-9 w-9 animate-pulse rounded-full bg-gradient-to-br from-brand-500 to-amber-600 opacity-80 dark:from-brand-400 dark:to-amber-500" />
          <p className="text-sm font-medium text-slate-600 dark:text-slate-300">{t("creditsPage.loadingWallet")}</p>
        </div>
      </div>
    );
  }

  if (me === null) {
    return (
      <div className="mx-auto max-w-xl space-y-4 py-8">
        <button type="button" onClick={onBack} className="text-sm font-semibold text-brand-700 dark:text-brand-300">
          {t("common.back")}
        </button>
        <p className="text-sm text-slate-600 dark:text-slate-400">{t("creditsPage.loadFail")}</p>
        <button
          type="button"
          onClick={() => reload()}
          className="rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white"
        >
          {t("common.retry")}
        </button>
      </div>
    );
  }

  if (me.feature_enabled === false) {
    return (
      <div className="mx-auto max-w-xl space-y-4 py-8">
        <button type="button" onClick={onBack} className="text-sm font-semibold text-brand-700 dark:text-brand-300">
          {t("common.back")}
        </button>
        <div className="glass-panel rounded-2xl p-5 text-sm text-slate-600 dark:text-slate-400">
          {typeof me.message === "string" ? me.message : t("creditsPage.featureOff")}
        </div>
      </div>
    );
  }

  const exp = me.credits_expire_at ? new Date(String(me.credits_expire_at)) : null;
  const locTag = appLocaleTag(i18n.language);
  const expStr =
    exp && !Number.isNaN(exp.getTime())
      ? exp.toLocaleString(locTag, { dateStyle: "medium", timeStyle: "short" })
      : null;

  const rawBal =
    typeof me.balance_mru === "number"
      ? me.balance_mru
      : typeof me.credit_balance === "number"
        ? me.credit_balance
        : null;
  const balanceStr =
    rawBal != null && Number.isFinite(rawBal)
      ? rawBal.toLocaleString(locTag, { minimumFractionDigits: 0, maximumFractionDigits: 2 })
      : "—";

  const accountOk = me.can_use_features !== false;

  return (
    <div className="relative mx-auto max-w-2xl space-y-10 pb-20">
      {/* Ambient decor (no layout shift) */}
      <div
        className="pointer-events-none absolute inset-0 -z-10 overflow-hidden rounded-[2.5rem] opacity-90"
        aria-hidden
      >
        <div className="absolute -left-1/4 top-0 h-[28rem] w-[28rem] rounded-full bg-gradient-to-br from-brand-400/28 via-amber-400/18 to-transparent blur-3xl motion-safe:animate-blob-drift dark:from-brand-600/18 dark:via-amber-600/12" />
        <div className="absolute -right-1/4 bottom-0 h-[24rem] w-[24rem] rounded-full bg-gradient-to-tl from-rose-300/22 via-brand-400/15 to-transparent blur-3xl motion-safe:animate-blob-drift-reverse dark:from-rose-600/12 dark:via-brand-500/12" />
        <div className="absolute inset-0 bg-dot-grid opacity-[0.35] dark:opacity-25" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={onBack}
          className="group inline-flex items-center gap-2 rounded-full border border-slate-200/80 bg-white/60 px-3 py-1.5 text-sm font-semibold text-slate-700 shadow-sm backdrop-blur-sm transition hover:border-brand-300/70 hover:bg-white hover:text-brand-900 dark:border-slate-700/80 dark:bg-slate-900/50 dark:text-slate-200 dark:hover:border-brand-700/60 dark:hover:bg-slate-900/80 dark:hover:text-white"
        >
          {t("common.back")}
        </button>
      </div>

      {/* Hero wallet */}
      <section
        className="relative overflow-hidden rounded-[1.75rem] ring-gradient-brand motion-safe:animate-fade-in-up"
        style={{ animationDelay: "40ms" }}
      >
        <div className="absolute inset-0 bg-gradient-to-br from-brand-600/[0.08] via-amber-500/[0.05] to-rose-500/[0.05] dark:from-brand-500/10 dark:via-amber-600/8 dark:to-rose-600/8" />
        <div className="absolute -right-16 -top-20 h-56 w-56 rounded-full bg-gradient-to-br from-brand-400/30 to-amber-600/22 blur-3xl dark:from-brand-500/20 dark:to-amber-500/15" />
        <div className="absolute -bottom-12 -left-10 h-44 w-44 rounded-full bg-gradient-to-tr from-rose-300/25 to-transparent blur-2xl dark:from-rose-600/12" />

        <div className="relative border border-white/80 bg-white/70 p-6 shadow-soft backdrop-blur-xl dark:border-white/10 dark:bg-slate-950/65 sm:p-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0 flex-1 space-y-4">
              <div className="flex items-start gap-3">
                <span className="mt-0.5 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-600 to-amber-600 text-white shadow-lg shadow-brand-600/25 dark:shadow-brand-900/40">
                  <IconWallet className="h-6 w-6" />
                </span>
                <div className="min-w-0">
                  <h1 className="font-display text-xl font-bold tracking-tight text-slate-900 dark:text-white sm:text-2xl">
                    {t("creditsPage.title")}
                  </h1>
                  <p className="mt-2 border-s-[3px] border-brand-400/70 py-0.5 ps-3 text-[12px] leading-relaxed text-slate-600 dark:border-brand-500/60 dark:text-slate-400">
                    {t("creditsPage.intro", { transcription: ENGINE_TRANSCRIPTION, course: ENGINE_COURSE })}
                  </p>
                </div>
              </div>

              <div className="relative">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-slate-500">
                  {t("creditsPage.balance")}
                </p>
                <p className="font-display text-fluid-hero mt-1 font-black tabular-nums tracking-tight text-gradient-brand">
                  {balanceStr}
                  <span className="ms-2 align-middle text-xl font-bold text-brand-500/90 dark:text-brand-400/90">
                    MRU
                  </span>
                </p>
              </div>
            </div>

            <div className="w-full shrink-0 lg:max-w-[15.5rem]">
              <div
                className={`relative overflow-hidden rounded-2xl border bg-slate-50/95 p-4 shadow-inner dark:bg-slate-900/85 ${
                  accountOk
                    ? "border-emerald-200/80 dark:border-emerald-900/50"
                    : "border-amber-200/90 dark:border-amber-900/50"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="relative flex h-2.5 w-2.5" title={t("creditsPage.statusDot")}>
                    <span
                      className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-40 ${
                        accountOk ? "bg-emerald-500" : "bg-amber-500"
                      }`}
                    />
                    <span
                      className={`relative inline-flex h-2.5 w-2.5 rounded-full ${
                        accountOk ? "bg-emerald-500" : "bg-amber-500"
                      }`}
                    />
                  </span>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                    {t("creditsPage.validity")}
                  </span>
                </div>
                <p className="mt-2 text-sm font-semibold leading-snug text-slate-800 dark:text-slate-100" lang={locTag}>
                  {expStr ?? t("creditsPage.validityUnknown")}
                </p>
                {me.can_use_features === false ? (
                  <p className="mt-3 text-[13px] font-medium leading-relaxed text-amber-800 dark:text-amber-200">
                    {String(me.block_reason || "")}
                  </p>
                ) : (
                  <p className="mt-3 inline-flex items-center gap-1.5 text-[13px] font-semibold text-emerald-700 dark:text-emerald-400">
                    <span className="text-base leading-none">✓</span>
                    {t("creditsPage.canUse")}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      <form
        onSubmit={submitProof}
        className="relative space-y-6 rounded-[1.75rem] border border-white/70 bg-white/80 p-6 shadow-soft backdrop-blur-xl motion-safe:animate-fade-in-up dark:border-slate-700/70 dark:bg-slate-900/80 sm:p-8"
        style={{ animationDelay: "90ms" }}
      >
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="font-display text-lg font-bold text-slate-900 dark:text-white">{t("creditsPage.requestTopUp")}</h2>
            <p id="wallet-formats" className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
              {t("creditsPage.formats")}
            </p>
          </div>
        </div>

        <div className="space-y-3">
          <p className="text-xs font-semibold text-slate-700 dark:text-slate-300">{t("creditsPage.payNumbersTitle")}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              { label: t("creditsPage.bankily"), num: "42986738", accent: "from-brand-600/90 to-rose-600/75" },
              { label: t("creditsPage.sedad"), num: "32164356", accent: "from-amber-600/85 to-brand-600/85" },
            ].map(({ label, num, accent }) => (
              <div
                key={num}
                className="group relative overflow-hidden rounded-2xl border border-slate-200/90 bg-gradient-to-br p-[1px] dark:border-slate-700/80"
              >
                <div
                  className={`absolute inset-0 bg-gradient-to-br opacity-40 blur-xl transition-opacity group-hover:opacity-70 dark:opacity-30 ${accent}`}
                />
                <div className="relative flex flex-col gap-2 rounded-[0.9rem] bg-white/95 p-4 dark:bg-slate-950/90">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                    {label}
                  </span>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-display text-lg font-bold tabular-nums text-slate-900 dark:text-white">{num}</span>
                    <button
                      type="button"
                      onClick={() => void copyNumber(num)}
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-slate-200/90 bg-slate-50 px-2.5 py-1.5 text-[11px] font-bold text-brand-700 shadow-sm transition hover:border-brand-300 hover:bg-brand-50 hover:text-brand-900 dark:border-slate-600 dark:bg-slate-900 dark:text-brand-300 dark:hover:border-brand-700 dark:hover:bg-slate-800"
                      title={t("creditsPage.copyNumber")}
                    >
                      <IconCopy className="h-3.5 w-3.5" />
                      {t("creditsPage.copyNumber")}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,.png,.jpg,.jpeg,.webp,.gif"
            className="sr-only"
            disabled={busy}
            aria-describedby="wallet-formats"
            onChange={(e) => setFileFromList(e.target.files)}
          />
          <div
            role="button"
            tabIndex={busy ? -1 : 0}
            aria-disabled={busy}
            onKeyDown={(e) => {
              if (busy) return;
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                fileRef.current?.click();
              }
            }}
            onClick={() => !busy && fileRef.current?.click()}
            onDragEnter={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setDragActive(true);
            }}
            onDragLeave={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (!e.currentTarget.contains(e.relatedTarget)) setDragActive(false);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setDragActive(false);
              if (!busy) setFileFromList(e.dataTransfer.files);
            }}
            className={`group relative flex w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-4 py-10 text-center transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 ${
              busy ? "cursor-not-allowed opacity-60" : ""
            } ${
              dragActive
                ? "border-brand-500 bg-brand-50/90 dark:border-brand-400 dark:bg-brand-950/45"
                : "border-slate-300/90 bg-slate-50/50 hover:border-brand-400/80 hover:bg-brand-50/40 dark:border-slate-600 dark:bg-slate-900/40 dark:hover:border-brand-600 dark:hover:bg-brand-950/28"
            }`}
          >
            <span
              className={`inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br text-white shadow-lg transition group-hover:scale-105 ${
                file
                  ? "from-emerald-500 to-teal-600 shadow-emerald-500/25"
                  : "from-brand-600 to-amber-600 shadow-brand-600/25"
              }`}
            >
              {file ? (
                <span className="text-2xl" aria-hidden>
                  ✓
                </span>
              ) : (
                <IconUpload className="h-7 w-7" />
              )}
            </span>
            <div>
              <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                {dragActive ? t("creditsPage.dropActiveHint") : t("creditsPage.dropHint")}
              </p>
              {file ? (
                <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
                  <span className="max-w-full truncate rounded-full bg-slate-900/[0.06] px-3 py-1 text-xs font-medium text-slate-700 dark:bg-white/10 dark:text-slate-200">
                    <span className="text-slate-500 dark:text-slate-400">{t("creditsPage.selectedFile")}: </span>
                    {file.name}
                  </span>
                  <button
                    type="button"
                    className="text-xs font-bold text-brand-600 underline-offset-2 hover:underline dark:text-brand-400"
                    onClick={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      setFile(null);
                      if (fileRef.current) fileRef.current.value = "";
                    }}
                  >
                    {t("creditsPage.clearFile")}
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <button
          type="submit"
          disabled={busy || !file}
          className="hover-lift group relative w-full overflow-hidden rounded-2xl bg-gradient-to-r from-brand-600 via-amber-500 to-rose-500 px-6 py-3.5 text-sm font-bold text-white shadow-glow transition hover:brightness-[1.05] disabled:cursor-not-allowed disabled:opacity-45 dark:from-brand-500 dark:via-amber-500 dark:to-rose-600"
        >
          <span className="relative z-10">{t("creditsPage.sendProof")}</span>
          <span
            className="pointer-events-none absolute inset-0 translate-x-[-100%] bg-gradient-to-r from-transparent via-white/20 to-transparent transition duration-700 group-hover:translate-x-[100%] motion-reduce:hidden"
            aria-hidden
          />
        </button>
      </form>

      <div className="space-y-4 motion-safe:animate-fade-in-up" style={{ animationDelay: "140ms" }}>
        <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.15em] text-slate-500 dark:text-slate-400">
          <span className="h-px flex-1 bg-gradient-to-r from-transparent to-slate-300/80 dark:to-slate-600/80" aria-hidden />
          {t("creditsPage.recentTitle")}
          <span className="h-px flex-1 bg-gradient-to-l from-transparent to-slate-300/80 dark:to-slate-600/80" aria-hidden />
        </h3>
        <ul className="space-y-3">
          {requests.length === 0 ? (
            <li className="rounded-2xl border border-dashed border-slate-200/90 bg-slate-50/50 px-4 py-8 text-center text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-500">
              {t("creditsPage.emptyRequests")}
            </li>
          ) : (
            requests.map((r) => (
              <li
                key={r.id}
                className="relative overflow-hidden rounded-2xl border border-slate-200/85 bg-gradient-to-r from-white/95 to-slate-50/90 px-4 py-3.5 shadow-sm transition hover:border-brand-200/90 hover:shadow-md dark:border-slate-700/85 dark:from-slate-900/90 dark:to-slate-950/90 dark:hover:border-brand-900/50"
              >
                <span
                  className={`absolute inset-y-0 start-0 w-1 rounded-e-sm ${
                    r.status === "approved"
                      ? "bg-emerald-500"
                      : r.status === "rejected"
                        ? "bg-rose-500"
                        : "bg-amber-400"
                  }`}
                  aria-hidden
                />
                <div className="ps-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-display font-bold text-slate-900 dark:text-white">#{r.id}</span>
                    <span className="text-[11px] font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                      {statusLabels[r.status] || r.status}
                    </span>
                  </div>
                  {r.created_at ? (
                    <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                      {new Date(String(r.created_at)).toLocaleString(locTag)}
                    </div>
                  ) : null}
                  {typeof r.granted_mru_approx === "number" ? (
                    <div className="mt-1.5 inline-flex rounded-lg bg-brand-500/10 px-2 py-0.5 text-sm font-bold text-brand-700 dark:bg-brand-400/15 dark:text-brand-300">
                      +{r.granted_mru_approx} MRU
                    </div>
                  ) : typeof r.credits_granted === "number" ? (
                    <div className="mt-1.5 inline-flex rounded-lg bg-brand-500/10 px-2 py-0.5 text-sm font-bold text-brand-700 dark:bg-brand-400/15 dark:text-brand-300">
                      +{`${t("creditsPage.internalUnits")} ${r.credits_granted}`}
                    </div>
                  ) : null}
                  {r.admin_note ? (
                    <div className="mt-2 text-[11px] italic leading-relaxed text-slate-600 dark:text-slate-500">
                      {t("creditsPage.notePrefix")} {r.admin_note}
                    </div>
                  ) : null}
                </div>
              </li>
            ))
          )}
        </ul>
      </div>

      <TelegramLinkCard />

      <ReferralCard />
    </div>
  );
}
