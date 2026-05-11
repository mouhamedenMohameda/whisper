import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { apiUrl, getAuthHeaders, parseJsonResponse, safeJsonDetail } from "../utils/api.js";

function clampStr(s, max = 24000) {
  const t = String(s || "").trim();
  if (!t) return "";
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

/** MRU depuis l’API (nombre ou chaîne) — jamais négatif. */
function parseNonnegMru(v) {
  if (v == null || v === "") return 0;
  const n = typeof v === "number" ? v : parseFloat(String(v).replace(",", "."));
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, n);
}

/** MRU facturé (coût chat) : toujours ≥ 0 ; |valeur| si signe erroné (aligné sur debit_wallet_units). */
function parseBilledMruDisplay(v) {
  if (v == null || v === "") return 0;
  const n = typeof v === "number" ? v : parseFloat(String(v).replace(",", "."));
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.abs(n));
}

function parseNonnegInt(v) {
  if (v == null || v === "") return null;
  const n = typeof v === "number" ? v : parseInt(String(v), 10);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, n);
}

const mdChat = {
  h1({ children }) {
    return <h3 className="mb-2 mt-1 font-display text-lg font-extrabold text-slate-900 dark:text-slate-50">{children}</h3>;
  },
  h2({ children }) {
    return <h4 className="mb-2 mt-4 font-display text-base font-extrabold text-slate-900 dark:text-slate-50">{children}</h4>;
  },
  h3({ children }) {
    return <h5 className="mb-2 mt-3 text-sm font-bold text-slate-900 dark:text-slate-50">{children}</h5>;
  },
  p({ children }) {
    return <p className="mb-3 text-sm leading-relaxed text-slate-800 dark:text-slate-200">{children}</p>;
  },
  ul({ children }) {
    return <ul className="mb-3 list-disc space-y-1 pl-5 text-sm text-slate-800 dark:text-slate-200">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="mb-3 list-decimal space-y-1 pl-5 text-sm text-slate-800 dark:text-slate-200">{children}</ol>;
  },
  li({ children }) {
    return <li className="leading-relaxed">{children}</li>;
  },
  strong({ children }) {
    return <strong className="font-semibold text-slate-950 dark:text-white">{children}</strong>;
  },
  hr() {
    return <hr className="my-5 border-slate-200/70 dark:border-slate-700/70" />;
  },
  table({ children }) {
    return (
      <div className="mb-4 overflow-x-auto rounded-xl border border-slate-200/80 bg-white/70 dark:border-slate-700/70 dark:bg-slate-950/35">
        <table className="w-full border-collapse text-sm">{children}</table>
      </div>
    );
  },
  thead({ children }) {
    return <thead className="bg-slate-50/80 dark:bg-slate-900/40">{children}</thead>;
  },
  th({ children }) {
    return <th className="border-b border-slate-200/80 px-3 py-2 text-left text-[11px] font-extrabold text-slate-700 dark:border-slate-700/70 dark:text-slate-200">{children}</th>;
  },
  td({ children }) {
    return <td className="border-b border-slate-200/60 px-3 py-2 align-top text-sm text-slate-800 dark:border-slate-700/60 dark:text-slate-200">{children}</td>;
  },
  code({ inline, children }) {
    if (inline) {
      return <code className="rounded bg-slate-900/[0.06] px-1 py-0.5 text-[0.9em] text-slate-900 dark:bg-white/[0.07] dark:text-slate-50">{children}</code>;
    }
    return (
      <pre className="mb-4 overflow-x-auto rounded-xl border border-slate-200/80 bg-slate-950 px-4 py-3 text-[12px] text-slate-100 dark:border-slate-800">
        <code>{children}</code>
      </pre>
    );
  },
  blockquote({ children }) {
    return (
      <blockquote className="mb-4 border-l-4 border-brand-400/70 pl-3 text-sm italic text-slate-700 dark:border-brand-500/60 dark:text-slate-300">
        {children}
      </blockquote>
    );
  },
};

/**
 * SSE over fetch (supports Authorization headers).
 * @param {string} url
 * @param {RequestInit} init
 * @param {(evt: any) => void} onEvent
 */
async function fetchSse(url, init, onEvent) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(await safeJsonDetail(res));
  if (!res.body) throw new Error("Réponse streaming indisponible.");

  const reader = res.body.getReader();
  const dec = new TextDecoder("utf-8");
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });

    // SSE frames separated by blank line
    while (true) {
      const ix = buf.indexOf("\n\n");
      if (ix < 0) break;
      const frame = buf.slice(0, ix);
      buf = buf.slice(ix + 2);

      const lines = frame.split("\n");
      for (const ln of lines) {
        const m = ln.match(/^data:\s*(.*)$/);
        if (!m) continue;
        const payload = m[1] || "";
        if (!payload.trim()) continue;
        try {
          onEvent(JSON.parse(payload));
        } catch {
          // ignore malformed events
        }
      }
    }
  }
}

export default function ChatPage({ onBack, onWalletUpdated }) {
  const { t } = useTranslation();
  const [threads, setThreads] = useState([]);
  const [activeThreadId, setActiveThreadId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [lastBilling, setLastBilling] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const scrollRef = useRef(null);

  const threadTotals = useMemo(() => {
    let mru = 0;
    let units = 0;
    let tokens = 0;
    for (const m of messages) {
      if (m?.billed_mru != null) mru += parseBilledMruDisplay(m.billed_mru);
      const du = parseNonnegInt(m?.debit_wallet_units);
      if (du != null) units += du;
      const pt = Number(m?.prompt_tokens ?? 0) || 0;
      const ct = Number(m?.completion_tokens ?? 0) || 0;
      tokens += pt + ct;
    }
    return { mru, units, tokens };
  }, [messages]);

  const activeThread = useMemo(
    () => threads.find((x) => String(x.id) === String(activeThreadId)) || null,
    [threads, activeThreadId],
  );

  const loadThreads = useCallback(async () => {
    setErr("");
    try {
      const res = await fetch(apiUrl("/api/chat/threads"), { headers: getAuthHeaders(false) });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage || "Erreur.");
      const rows = Array.isArray(p.data) ? p.data : [];
      setThreads(rows);
      if (!activeThreadId && rows.length) setActiveThreadId(rows[0].id);
    } catch (e) {
      setErr(String(e?.message || e || ""));
    }
  }, [activeThreadId]);

  const loadMessages = useCallback(async (threadId) => {
    if (!threadId) {
      setMessages([]);
      setLastBilling(null);
      return;
    }
    setErr("");
    try {
      const res = await fetch(apiUrl(`/api/chat/threads/${encodeURIComponent(threadId)}/messages`), {
        headers: getAuthHeaders(false),
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage || "Erreur.");
      const rows = Array.isArray(p.data) ? p.data : [];
      setMessages(rows);
      // Dernier coût connu : dernière réponse assistant qui a un billed_mru ; sinon remise à zéro (évite d’afficher l’ancienne discussion)
      let billingFromRows = false;
      for (let i = rows.length - 1; i >= 0; i--) {
        if (rows[i]?.role === "assistant" && rows[i]?.billed_mru != null) {
          setLastBilling({
            billedMru: parseBilledMruDisplay(rows[i].billed_mru),
            units: parseNonnegInt(rows[i]?.debit_wallet_units),
            usd:
              rows[i]?.provider_usd != null
                ? Math.max(0, parseFloat(String(rows[i].provider_usd).replace(",", ".")) || 0)
                : null,
            mainTok:
              (Number(rows[i]?.prompt_tokens ?? 0) || 0) + (Number(rows[i]?.completion_tokens ?? 0) || 0),
            sumTok: 0,
          });
          billingFromRows = true;
          break;
        }
      }
      if (!billingFromRows) setLastBilling(null);
    } catch (e) {
      setErr(String(e?.message || e || ""));
    }
  }, []);

  useEffect(() => {
    void loadThreads();
  }, [loadThreads]);

  useEffect(() => {
    void loadMessages(activeThreadId);
  }, [activeThreadId, loadMessages]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  useEffect(() => {
    if (!deleteTarget) return;
    const onKey = (e) => {
      if (e.key === "Escape") setDeleteTarget(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [deleteTarget]);

  const createThread = useCallback(async () => {
    setErr("");
    setBusy(true);
    try {
      const res = await fetch(apiUrl("/api/chat/threads"), {
        method: "POST",
        headers: { ...getAuthHeaders(true), "Content-Type": "application/json" },
        body: JSON.stringify({ title: t("chat.newThreadDefaultTitle") }),
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage || "Erreur.");
      const tid = p.data?.id;
      await loadThreads();
      if (tid != null) setActiveThreadId(tid);
      setMessages([]);
    } catch (e) {
      setErr(String(e?.message || e || ""));
    } finally {
      setBusy(false);
    }
  }, [loadThreads, t]);

  const executeDeleteThread = useCallback(
    async (threadId) => {
      if (!threadId || busy) return;
      setErr("");
      setBusy(true);
      try {
        const res = await fetch(apiUrl(`/api/chat/threads/${encodeURIComponent(threadId)}`), {
          method: "DELETE",
          headers: getAuthHeaders(false),
        });
        const p = await parseJsonResponse(res);
        if (!p.ok) throw new Error(p.errorMessage || "Erreur.");

        setThreads((rows) => rows.filter((x) => String(x.id) !== String(threadId)));
        if (String(activeThreadId) === String(threadId)) {
          setActiveThreadId((prev) => (String(prev) === String(threadId) ? null : prev));
          setMessages([]);
          setLastBilling(null);
        }
        setDeleteTarget(null);
        await loadThreads();
      } catch (e) {
        setErr(String(e?.message || e || ""));
      } finally {
        setBusy(false);
      }
    },
    [activeThreadId, busy, loadThreads],
  );

  const send = useCallback(async () => {
    const content = clampStr(text);
    if (!content || busy) return;

    setErr("");
    setLastBilling(null);
    setBusy(true);
    setText("");

    let threadId = activeThreadId;
    try {
      if (!threadId) {
        const resNew = await fetch(apiUrl("/api/chat/threads"), {
          method: "POST",
          headers: { ...getAuthHeaders(true), "Content-Type": "application/json" },
          body: JSON.stringify({ title: t("chat.newThreadDefaultTitle") }),
        });
        const pNew = await parseJsonResponse(resNew);
        if (!pNew.ok) throw new Error(pNew.errorMessage || "Erreur.");
        threadId = pNew.data?.id;
        await loadThreads();
        setActiveThreadId(threadId);
      }

      const localUser = { id: `local-u-${Date.now()}`, role: "user", content };
      const localAsst = { id: `local-a-${Date.now()}`, role: "assistant", content: "" };
      setMessages((m) => [...m, localUser, localAsst]);

      await fetchSse(
        apiUrl(`/api/chat/threads/${encodeURIComponent(threadId)}/messages`),
        {
          method: "POST",
          headers: { ...getAuthHeaders(true), "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        },
        (evt) => {
          if (evt?.error) {
            setErr(String(evt.error || ""));
            return;
          }
          if (typeof evt?.delta === "string") {
            const d = evt.delta;
            setMessages((m) => {
              const out = [...m];
              for (let i = out.length - 1; i >= 0; i--) {
                if (out[i]?.role === "assistant") {
                  out[i] = { ...out[i], content: String(out[i].content || "") + d };
                  break;
                }
              }
              return out;
            });
            return;
          }
          if (evt?.done) {
            if (evt.thread && typeof evt.thread === "object" && evt.thread.id != null && typeof evt.thread.title === "string") {
              setThreads((rows) =>
                rows.map((r) => (String(r.id) === String(evt.thread.id) ? { ...r, title: evt.thread.title } : r)),
              );
            }
            if (evt.usage && typeof evt.usage === "object") {
              setLastBilling({
                billedMru: parseBilledMruDisplay(evt.usage?.billed_mru_total),
                units: parseNonnegInt(evt.usage?.debit_wallet_units) ?? 0,
                usd: parseNonnegMru(evt.usage?.provider_usd_total),
                mainTok: (Number(evt.usage?.main_prompt_tokens ?? 0) || 0) + (Number(evt.usage?.main_completion_tokens ?? 0) || 0),
                sumTok:
                  (Number(evt.usage?.summary_prompt_tokens ?? 0) || 0) +
                  (Number(evt.usage?.summary_completion_tokens ?? 0) || 0),
              });
            }
            if (evt.wallet && typeof onWalletUpdated === "function") {
              onWalletUpdated();
            }
          }
        },
      );

      // Sync from server for consistency (ids, etc.)
      await loadMessages(threadId);
      await loadThreads();
    } catch (e) {
      setErr(String(e?.message || e || ""));
    } finally {
      setBusy(false);
    }
  }, [activeThreadId, busy, loadMessages, loadThreads, onWalletUpdated, t, text]);

  return (
    <div className="relative grid gap-4 lg:grid-cols-[18rem_1fr]">
      <section className="glass-panel rounded-3xl p-4 shadow-soft">
        <div className="flex items-center justify-between gap-2">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-400">
            {t("chat.threads")}
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={createThread}
            className="rounded-full border border-slate-200/90 bg-white/75 px-3 py-2 text-[11px] font-bold text-slate-800 shadow-sm transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
          >
            {t("chat.newThread")}
          </button>
        </div>
        <div className="mt-3 space-y-2">
          {threads.length === 0 ? (
            <p className="text-xs text-slate-500 dark:text-slate-400">{t("chat.noThreads")}</p>
          ) : (
            threads.map((th) => (
              <div
                key={th.id}
                className={`flex w-full items-center gap-2 rounded-2xl border px-3 py-2 text-left text-xs font-semibold transition ${
                  String(th.id) === String(activeThreadId)
                    ? "border-brand-400/50 bg-brand-500/10 text-brand-900 dark:border-brand-400/40 dark:bg-brand-500/15 dark:text-brand-100"
                    : "border-slate-200/90 bg-white/70 text-slate-800 hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-100 dark:hover:bg-slate-800"
                }`}
              >
                <button type="button" onClick={() => setActiveThreadId(th.id)} className="min-w-0 flex-1 text-left">
                  <div className="truncate">{String(th.title || t("chat.untitled"))}</div>
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setDeleteTarget({
                      id: th.id,
                      title: String(th.title || t("chat.untitled")),
                    });
                  }}
                  className="shrink-0 rounded-full border border-slate-200/70 bg-white/70 px-2 py-1 text-[11px] font-bold text-slate-700 transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-700/70 dark:bg-slate-950/35 dark:text-slate-200 dark:hover:bg-slate-900/70"
                  title={t("chat.delete")}
                >
                  {t("chat.delete")}
                </button>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="glass-panel flex min-h-[60vh] flex-col rounded-3xl shadow-soft">
        <div className="flex items-center justify-between gap-3 border-b border-slate-200/70 px-5 py-4 dark:border-slate-700/60">
          <div className="min-w-0">
            <div className="truncate text-sm font-extrabold text-slate-900 dark:text-slate-50">
              {activeThread?.title || t("chat.title")}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
              <span>{t("chat.subtitle")}</span>
              {parseBilledMruDisplay(threadTotals.mru) > 0 ? (
                <span className="rounded-full border border-amber-200/70 bg-amber-50/80 px-2 py-0.5 text-[10px] font-bold text-amber-950 dark:border-amber-900/50 dark:bg-amber-950/35 dark:text-amber-100">
                  {t("chat.threadTotalMru", { mru: parseBilledMruDisplay(threadTotals.mru).toFixed(6) })}
                </span>
              ) : null}
              {threadTotals.units > 0 ? (
                <span className="rounded-full border border-slate-200/80 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-slate-700/70 dark:bg-slate-950/40 dark:text-slate-300">
                  {t("chat.threadTotalUnits", { n: String(threadTotals.units) })}
                </span>
              ) : null}
            </div>
          </div>
          <button
            type="button"
            onClick={onBack}
            className="rounded-full border border-slate-200/90 bg-white/75 px-3 py-2 text-[11px] font-bold text-slate-800 shadow-sm transition hover:bg-white dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-100 dark:hover:bg-slate-800"
          >
            {t("common.back")}
          </button>
        </div>

        {err ? (
          <div className="px-5 pt-4 text-xs font-semibold text-rose-700 dark:text-rose-300">{err}</div>
        ) : null}

        <div ref={scrollRef} className="flex-1 space-y-3 overflow-auto px-5 py-4">
          {messages.length === 0 ? (
            <div className="text-sm text-slate-500 dark:text-slate-400">{t("chat.empty")}</div>
          ) : (
            messages.map((m) => {
              const role = String(m.role || "");
              const mine = role === "user";
              return (
                <div key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[min(100%,54rem)] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
                      mine
                        ? "bg-gradient-to-br from-brand-600 to-brand-500 text-white"
                        : "border border-slate-200/80 bg-white/80 text-slate-900 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-50"
                    }`}
                  >
                    {mine ? (
                      String(m.content || "")
                    ) : (
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm, remarkMath]}
                        rehypePlugins={[rehypeKatex]}
                        components={mdChat}
                      >
                        {String(m.content || "")}
                      </ReactMarkdown>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="border-t border-slate-200/70 px-5 py-4 dark:border-slate-700/60">
          {lastBilling ? (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-amber-200/80 bg-amber-50/90 px-3 py-1 text-[11px] font-bold text-amber-950 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-100">
                {t("chat.billedLine", { mru: parseBilledMruDisplay(lastBilling.billedMru).toFixed(6) })}
              </span>
              <span className="rounded-full border border-slate-200/80 bg-white/70 px-3 py-1 text-[11px] font-semibold text-slate-700 dark:border-slate-700/70 dark:bg-slate-950/40 dark:text-slate-300">
                {t("chat.debitedUnitsLine", {
                  n: lastBilling.units != null ? String(lastBilling.units) : "—",
                })}
              </span>
              <span className="rounded-full border border-slate-200/80 bg-white/70 px-3 py-1 text-[11px] font-semibold text-slate-700 dark:border-slate-700/70 dark:bg-slate-950/40 dark:text-slate-300">
                {t("chat.tokensLine", { n: String(lastBilling.mainTok + lastBilling.sumTok) })}
              </span>
            </div>
          ) : null}
          <div className="flex gap-2">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") void send();
              }}
              disabled={busy}
              rows={2}
              placeholder={t("chat.inputPlaceholder")}
              className="min-h-[2.75rem] w-full resize-none rounded-2xl border border-slate-200/90 bg-white/85 px-4 py-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-400/20 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900/75 dark:text-slate-50"
            />
            <button
              type="button"
              disabled={busy || !clampStr(text)}
              onClick={send}
              className="shrink-0 rounded-2xl bg-brand-600 px-4 py-3 text-sm font-extrabold text-white shadow-glow transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-45"
              title={t("chat.sendHint")}
            >
              {busy ? t("chat.sending") : t("chat.send")}
            </button>
          </div>
          <div className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">{t("chat.shortcutHint")}</div>
        </div>
      </section>

      {deleteTarget ? (
        <div
          className="fixed inset-0 z-[240] flex items-end justify-center p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
          aria-labelledby="chat-delete-modal-title"
        >
          <button
            type="button"
            className="absolute inset-0 bg-slate-900/50 backdrop-blur-[2px]"
            aria-label={t("chat.deleteModalBackdropSr")}
            onClick={() => !busy && setDeleteTarget(null)}
          />
          <div className="relative z-10 w-full max-w-md rounded-3xl border border-slate-200/90 bg-white/95 p-6 shadow-soft-lg backdrop-blur-md dark:border-slate-700 dark:bg-slate-900/95">
            <div className="flex items-start gap-3">
              <div
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-rose-500/15 dark:bg-rose-500/20"
                aria-hidden
              >
                <svg
                  className="h-5 w-5 text-rose-600 dark:text-rose-400"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.5}
                  aria-hidden
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
                  />
                </svg>
              </div>
              <div className="min-w-0 flex-1">
                <h2 id="chat-delete-modal-title" className="font-display text-base font-extrabold text-slate-900 dark:text-slate-50">
                  {t("chat.deleteModalTitle")}
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{t("chat.deleteModalBody")}</p>
                <p className="mt-3 truncate rounded-xl border border-slate-200/80 bg-slate-50/80 px-3 py-2 text-xs font-semibold text-slate-800 dark:border-slate-700/70 dark:bg-slate-950/50 dark:text-slate-200">
                  {deleteTarget.title}
                </p>
              </div>
            </div>
            <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                type="button"
                disabled={busy}
                onClick={() => setDeleteTarget(null)}
                className="rounded-2xl border border-slate-200/90 bg-white/80 px-4 py-2.5 text-sm font-bold text-slate-800 shadow-sm transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-100 dark:hover:bg-slate-800"
              >
                {t("chat.deleteCancel")}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void executeDeleteThread(deleteTarget.id)}
                className="rounded-2xl bg-rose-600 px-4 py-2.5 text-sm font-extrabold text-white shadow-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-45"
              >
                {busy ? t("chat.sending") : t("chat.deleteConfirmBtn")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

