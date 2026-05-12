import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiUrl, getAuthHeaders, parseJsonResponse } from "../utils/api.js";
import { appLocaleTag } from "../utils/locale.js";

function toast(msg, type) {
  window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg, type } }));
}

function IconBell(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden {...props}>
      <path
        d="M6 16V11a6 6 0 0 1 12 0v5l1.6 2.2A.5.5 0 0 1 19.2 19H4.8a.5.5 0 0 1-.4-.8L6 16Z"
        strokeLinejoin="round"
      />
      <path d="M10 20a2 2 0 0 0 4 0" strokeLinecap="round" />
    </svg>
  );
}

function IconCheck(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden {...props}>
      <path d="m5 12.5 4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconGift(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden {...props}>
      <rect x="3.5" y="9" width="17" height="11" rx="2" />
      <path d="M3.5 13.5h17" />
      <path d="M12 9v11" />
      <path
        d="M12 9c-1.5-2-3-3-4.5-2.4-1.2.5-1.2 2.4 0 2.9 1.2.5 3 0 4.5-.5Zm0 0c1.5-2 3-3 4.5-2.4 1.2.5 1.2 2.4 0 2.9-1.2.5-3 0-4.5-.5Z"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * @typedef {{
 *   id: number,
 *   kind: string,
 *   topup_request_id: number | null,
 *   credits_granted: number | null,
 *   mru_credited: number | null,
 *   admin_note: string | null,
 *   read: boolean,
 *   read_at: string | null,
 *   created_at: string | null,
 * }} Notification
 */

/**
 * @param {{ onBack: () => void, onUnreadChange?: (n: number) => void }} props
 */
export default function NotificationsPage({ onBack, onUnreadChange }) {
  const { t, i18n } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState(/** @type {Notification[]} */ ([]));
  const [unread, setUnread] = useState(0);
  const [busyAll, setBusyAll] = useState(false);
  const locTag = useMemo(() => appLocaleTag(i18n.language), [i18n.language]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(apiUrl("/api/notifications?limit=100"), {
        headers: getAuthHeaders(false),
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage || t("notifications.loadError"));
      const list = Array.isArray(p.data?.notifications) ? p.data.notifications : [];
      setItems(list);
      const u = Number(p.data?.unread ?? 0);
      setUnread(u);
      onUnreadChange?.(u);
    } catch (e) {
      toast(e?.message || t("notifications.loadError"), "error");
      setItems([]);
      setUnread(0);
      onUnreadChange?.(0);
    } finally {
      setLoading(false);
    }
  }, [t, onUnreadChange]);

  useEffect(() => {
    void load();
  }, [load]);

  const markOneRead = useCallback(
    async (/** @type {number} */ id) => {
      try {
        const res = await fetch(apiUrl(`/api/notifications/${id}/read`), {
          method: "POST",
          headers: getAuthHeaders(false),
        });
        const p = await parseJsonResponse(res);
        if (!p.ok) throw new Error(p.errorMessage);
        setItems((prev) =>
          prev.map((n) =>
            n.id === id ? { ...n, read: true, read_at: new Date().toISOString() } : n,
          ),
        );
        setUnread((u) => {
          const next = Math.max(0, u - 1);
          onUnreadChange?.(next);
          return next;
        });
      } catch (e) {
        toast(e?.message || t("notifications.markFail"), "error");
      }
    },
    [t, onUnreadChange],
  );

  const markAllRead = useCallback(async () => {
    if (busyAll || unread === 0) return;
    setBusyAll(true);
    try {
      const res = await fetch(apiUrl("/api/notifications/read-all"), {
        method: "POST",
        headers: getAuthHeaders(false),
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage);
      const now = new Date().toISOString();
      setItems((prev) => prev.map((n) => (n.read ? n : { ...n, read: true, read_at: now })));
      setUnread(0);
      onUnreadChange?.(0);
      toast(t("notifications.markAllOk"), "success");
    } catch (e) {
      toast(e?.message || t("notifications.markFail"), "error");
    } finally {
      setBusyAll(false);
    }
  }, [busyAll, unread, t, onUnreadChange]);

  return (
    <div className="relative mx-auto max-w-2xl space-y-8 pb-20">
      <div
        className="pointer-events-none absolute inset-0 -z-10 overflow-hidden rounded-[2.5rem] opacity-90"
        aria-hidden
      >
        <div className="absolute -left-1/4 top-0 h-[28rem] w-[28rem] rounded-full bg-gradient-to-br from-brand-400/25 via-amber-400/16 to-transparent blur-3xl motion-safe:animate-blob-drift dark:from-brand-600/16 dark:via-amber-600/10" />
        <div className="absolute -right-1/4 bottom-0 h-[24rem] w-[24rem] rounded-full bg-gradient-to-tl from-rose-300/20 via-brand-400/12 to-transparent blur-3xl motion-safe:animate-blob-drift-reverse dark:from-rose-600/12 dark:via-brand-500/10" />
        <div className="absolute inset-0 bg-dot-grid opacity-[0.3] dark:opacity-25" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={onBack}
          className="group inline-flex items-center gap-2 rounded-full border border-slate-200/80 bg-white/60 px-3 py-1.5 text-sm font-semibold text-slate-700 shadow-sm backdrop-blur-sm transition hover:border-brand-300/70 hover:bg-white hover:text-brand-900 dark:border-slate-700/80 dark:bg-slate-900/50 dark:text-slate-200 dark:hover:border-brand-700/60 dark:hover:bg-slate-900/80 dark:hover:text-white"
        >
          {t("common.back")}
        </button>
        {unread > 0 ? (
          <button
            type="button"
            onClick={() => void markAllRead()}
            disabled={busyAll}
            className="inline-flex items-center gap-1.5 rounded-full border border-brand-200/80 bg-brand-50/90 px-3 py-1.5 text-xs font-bold text-brand-800 shadow-sm transition hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-brand-800/60 dark:bg-brand-950/40 dark:text-brand-200 dark:hover:bg-brand-950/70"
          >
            <IconCheck className="h-3.5 w-3.5" />
            {t("notifications.markAll")}
          </button>
        ) : null}
      </div>

      <section
        className="relative overflow-hidden rounded-[1.75rem] ring-gradient-brand motion-safe:animate-fade-in-up"
        style={{ animationDelay: "40ms" }}
      >
        <div className="absolute inset-0 bg-gradient-to-br from-brand-600/[0.08] via-amber-500/[0.05] to-rose-500/[0.05] dark:from-brand-500/10 dark:via-amber-600/8 dark:to-rose-600/8" />
        <div className="absolute -right-16 -top-20 h-56 w-56 rounded-full bg-gradient-to-br from-brand-400/25 to-amber-600/20 blur-3xl dark:from-brand-500/16 dark:to-amber-500/12" />
        <div className="relative border border-white/80 bg-white/75 p-6 shadow-soft backdrop-blur-xl dark:border-white/10 dark:bg-slate-950/65 sm:p-8">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-600 to-amber-600 text-white shadow-lg shadow-brand-600/25 dark:shadow-brand-900/40">
              <IconBell className="h-6 w-6" />
            </span>
            <div className="min-w-0">
              <h1 className="font-display text-xl font-bold tracking-tight text-slate-900 dark:text-white sm:text-2xl">
                {t("notifications.title")}
              </h1>
              <p className="mt-2 border-s-[3px] border-brand-400/70 py-0.5 ps-3 text-[12px] leading-relaxed text-slate-600 dark:border-brand-500/60 dark:text-slate-400">
                {t("notifications.intro")}
              </p>
              {!loading ? (
                <p className="mt-3 text-[11px] font-semibold uppercase tracking-[0.15em] text-slate-500 dark:text-slate-400">
                  {unread > 0
                    ? t("notifications.unreadCount", { count: unread })
                    : t("notifications.allRead")}
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </section>

      <div className="space-y-3 motion-safe:animate-fade-in-up" style={{ animationDelay: "90ms" }}>
        {loading ? (
          <div className="rounded-2xl border border-dashed border-slate-200/90 bg-slate-50/50 px-4 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-400">
            {t("notifications.loading")}
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200/90 bg-slate-50/50 px-4 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-500">
            {t("notifications.empty")}
          </div>
        ) : (
          <ul className="space-y-3">
            {items.map((n) => {
              const isAdminGrant = n.kind === "admin_grant";
              const title = isAdminGrant
                ? t("notifications.adminGrantTitle")
                : t("notifications.topupApprovedTitle");
              const body = isAdminGrant
                ? t("notifications.adminGrantBody", {
                    mru: typeof n.mru_credited === "number" ? n.mru_credited : "—",
                  })
                : t("notifications.topupApprovedBody", {
                    id: n.topup_request_id ?? "—",
                    mru: typeof n.mru_credited === "number" ? n.mru_credited : "—",
                  });
              const accentBar = isAdminGrant ? "bg-emerald-500" : "bg-brand-500";
              const dot = !n.read;
              return (
                <li
                  key={n.id}
                  className={`relative overflow-hidden rounded-2xl border bg-gradient-to-r px-4 py-3.5 shadow-sm transition ${
                    n.read
                      ? "border-slate-200/85 from-white/95 to-slate-50/90 dark:border-slate-700/85 dark:from-slate-900/85 dark:to-slate-950/85"
                      : "border-brand-200/90 from-brand-50/80 to-amber-50/50 shadow-brand-100/40 dark:border-brand-900/55 dark:from-brand-950/35 dark:to-amber-950/25 dark:shadow-brand-900/20"
                  }`}
                >
                  <span className={`absolute inset-y-0 start-0 w-1 rounded-e-sm ${accentBar}`} aria-hidden />
                  <div className="ps-3">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-white shadow ${
                            isAdminGrant
                              ? "bg-gradient-to-br from-emerald-500 to-teal-600"
                              : "bg-gradient-to-br from-brand-600 to-amber-600"
                          }`}
                          aria-hidden
                        >
                          {isAdminGrant ? (
                            <IconGift className="h-4 w-4" />
                          ) : (
                            <IconCheck className="h-4 w-4" />
                          )}
                        </span>
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-baseline gap-2">
                            <span className="font-display font-bold text-slate-900 dark:text-white">
                              {title}
                            </span>
                            {dot ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-brand-500/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-brand-700 dark:bg-brand-400/15 dark:text-brand-300">
                                <span className="size-1.5 rounded-full bg-brand-500 motion-safe:animate-pulse dark:bg-brand-400" />
                                {t("notifications.newBadge")}
                              </span>
                            ) : null}
                          </div>
                          {n.created_at ? (
                            <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                              {new Date(String(n.created_at)).toLocaleString(locTag)}
                            </div>
                          ) : null}
                        </div>
                      </div>
                      {!n.read ? (
                        <button
                          type="button"
                          onClick={() => void markOneRead(n.id)}
                          className="inline-flex items-center gap-1 rounded-full border border-slate-200/90 bg-white/80 px-2.5 py-1 text-[11px] font-semibold text-slate-700 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800"
                          title={t("notifications.markOne")}
                        >
                          <IconCheck className="h-3 w-3" />
                          {t("notifications.markOne")}
                        </button>
                      ) : null}
                    </div>
                    <p className="mt-2 text-sm leading-relaxed text-slate-700 dark:text-slate-200">
                      {body}
                    </p>
                    {typeof n.mru_credited === "number" ? (
                      <div className="mt-2 inline-flex rounded-lg bg-brand-500/10 px-2 py-0.5 text-sm font-bold text-brand-700 dark:bg-brand-400/15 dark:text-brand-300">
                        +{n.mru_credited} MRU
                      </div>
                    ) : null}
                    {n.admin_note ? (
                      <div className="mt-2 text-[11px] italic leading-relaxed text-slate-600 dark:text-slate-400">
                        {t("creditsPage.notePrefix")} {n.admin_note}
                      </div>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
