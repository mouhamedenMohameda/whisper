import { useCallback, useEffect, useState } from "react";
import { apiUrl, getAuthHeaders, parseJsonResponse } from "../utils/api.js";

function toast(msg, type) {
  window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg, type } }));
}

const STATUS_FR = {
  pending: "En attente",
  approved: "Validée",
  rejected: "Refusée",
};

const USERS_PAGE_SIZE = 50;

/** @typedef {{ grantMode: "usd"|"mru", supplierUsd: string, directMru: string, extendDays: string, approveNote: string, rejectNote: string }} RowDraft */

const defaultDraft = () => ({
  grantMode: "usd",
  supplierUsd: "",
  directMru: "",
  extendDays: "",
  approveNote: "",
  rejectNote: "",
});

/**
 * @param {ReturnType<defaultDraft>} draft
 * @param {{ allowMruDebit?: boolean }} [opts]
 * @returns {{ ok: true, payload: Record<string, unknown> } | { ok: false, error: string }}
 */
function approvePayloadFromDraft(draft, opts = {}) {
  const allowMruDebit = Boolean(opts.allowMruDebit);
  /** @type {Record<string, unknown>} */
  const payload = {};
  const extRaw = String(draft.extendDays).trim();
  if (extRaw) {
    const d = parseInt(extRaw, 10);
    if (!Number.isFinite(d) || d < 1) {
      return {
        ok: false,
        error: "Nombre de jours de validité invalide — vide = défaut serveur.",
      };
    }
    payload.extend_validity_days = d;
  }
  if (draft.grantMode === "usd") {
    const u = Number(String(draft.supplierUsd || "").trim().replace(",", "."));
    if (!Number.isFinite(u) || u <= 0) {
      return { ok: false, error: "Indique un coût USD fournisseur (positif)." };
    }
    payload.supplier_cost_usd = u;
  } else {
    const m = Number(String(draft.directMru || "").trim().replace(",", "."));
    if (!Number.isFinite(m) || m === 0) {
      return {
        ok: false,
        error: allowMruDebit
          ? "Indique un montant MRU non nul (positif = crédit, négatif = retrait, ex. -50 ou -25.5)."
          : "Indique un montant MRU à créditer (positif).",
      };
    }
    if (!allowMruDebit && m < 0) {
      return { ok: false, error: "Indique un montant MRU à créditer (positif)." };
    }
    payload.mru_credit = m;
  }
  return { ok: true, payload };
}

export default function AdminTopUpsPage({ onBack }) {
  const [pricingParams, setPricingParams] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("pending");
  const [requests, setRequests] = useState([]);
  /** @type {[Record<number, RowDraft>, (v: Record<number, RowDraft> | ((prev: Record<number, RowDraft>) => Record<number, RowDraft>)) => void]} */
  const [draftById, setDraftById] = useState({});
  const [proofUrl, setProofUrl] = useState(null);
  const [proofTitle, setProofTitle] = useState("");
  const [busyRowId, setBusyRowId] = useState(null);

  const [manualEmailInput, setManualEmailInput] = useState("");
  /** @type {[Array<{ id: number; email: string; is_admin?: boolean; balance_mru_approx?: number; credit_balance?: number }>, (v:any)=>void]} */
  const [manualSearchHits, setManualSearchHits] = useState([]);
  const [manualSearchLoading, setManualSearchLoading] = useState(false);
  /** @type {[null | { id: number; email: string; is_admin?: boolean; balance_mru_approx?: number; credit_balance?: number }, (v:any)=>void]} */
  const [manualSelectedUser, setManualSelectedUser] = useState(null);
  const [manualDraft, setManualDraft] = useState(() => defaultDraft());
  const [manualBusy, setManualBusy] = useState(false);

  /** @type {["topups" | "users", (v: "topups" | "users") => void]} */
  const [adminSection, setAdminSection] = useState("topups");
  const [usersFilterInput, setUsersFilterInput] = useState("");
  const [usersDebouncedFilter, setUsersDebouncedFilter] = useState("");
  const [usersLoading, setUsersLoading] = useState(false);
  /** @type {[Array<{ id: number; email: string; is_admin?: boolean; balance_mru_approx?: number; credit_balance?: number; credits_expire_at?: string | null; created_at?: string | null }>, (v: any) => void]} */
  const [usersRows, setUsersRows] = useState([]);
  const [usersTotal, setUsersTotal] = useState(0);
  const [usersOffset, setUsersOffset] = useState(0);

  const patchDraft = (id, patch) => {
    setDraftById((prev) => {
      const row = prev[id] ?? defaultDraft();
      return { ...prev, [id]: { ...row, ...patch } };
    });
  };

  const patchManualDraft = useCallback((patch) => {
    setManualDraft((prev) => ({ ...prev, ...patch }));
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const q = filter === "all" ? "" : `?status=${encodeURIComponent(filter)}`;
      const res = await fetch(apiUrl(`/api/admin/credit-topups${q}`), { headers: getAuthHeaders(false) });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage || "Liste inaccessible.");
      setRequests(p.data.requests || []);
    } catch (e) {
      toast(e?.message || "Erreur de chargement", "error");
      setRequests([]);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    if (adminSection !== "topups") return;
    reload();
  }, [reload, adminSection]);

  useEffect(() => {
    const t = setTimeout(() => setUsersDebouncedFilter(usersFilterInput.trim()), 360);
    return () => clearTimeout(t);
  }, [usersFilterInput]);

  useEffect(() => {
    setUsersOffset(0);
  }, [usersDebouncedFilter]);

  useEffect(() => {
    if (adminSection !== "users") return;
    let cancelled = false;
    (async () => {
      setUsersLoading(true);
      try {
        const qs = new URLSearchParams({
          limit: String(USERS_PAGE_SIZE),
          offset: String(usersOffset),
        });
        if (usersDebouncedFilter.length >= 2) qs.set("q", usersDebouncedFilter);
        const res = await fetch(apiUrl(`/api/admin/users?${qs}`), { headers: getAuthHeaders(false) });
        const p = await parseJsonResponse(res);
        if (cancelled) return;
        if (!p.ok) throw new Error(p.errorMessage || "Liste des comptes inaccessible.");
        setUsersRows(Array.isArray(p.data.users) ? p.data.users : []);
        setUsersTotal(typeof p.data.total === "number" ? p.data.total : 0);
      } catch (e) {
        if (!cancelled) {
          setUsersRows([]);
          setUsersTotal(0);
          toast(e?.message || "Erreur de chargement", "error");
        }
      } finally {
        if (!cancelled) setUsersLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [adminSection, usersOffset, usersDebouncedFilter]);

  /** Recherche utilisateurs (sans demande préalable). */
  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(() => {
      (async () => {
        const q = manualEmailInput.trim();
        if (q.length < 2) {
          setManualSearchHits([]);
          setManualSearchLoading(false);
          return;
        }
        setManualSearchLoading(true);
        try {
          const qs = encodeURIComponent(q);
          const res = await fetch(apiUrl(`/api/admin/users/search?q=${qs}`), { headers: getAuthHeaders(false) });
          const p = await parseJsonResponse(res);
          if (cancelled) return;
          if (!p.ok) throw new Error(p.errorMessage || "Recherche impossible.");
          setManualSearchHits(Array.isArray(p.data.users) ? p.data.users : []);
        } catch (e) {
          if (!cancelled) {
            setManualSearchHits([]);
            toast(e?.message || "Recherche impossible", "error");
          }
        } finally {
          if (!cancelled) setManualSearchLoading(false);
        }
      })();
    }, 360);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [manualEmailInput]);

  useEffect(() => {
    let ok = true;
    (async () => {
      try {
        const res = await fetch(apiUrl("/api/credits/pricing-info"));
        const data = await res.json().catch(() => null);
        if (!ok || !data || typeof data.mru_per_usd !== "number") return;
        setPricingParams({
          mruPerUsd: data.mru_per_usd,
          margin: data.customer_margin_multiplier,
          walletMicro: data.wallet_micro_per_mru ?? 10000,
        });
      } catch {
        /* silencieux : preview désactivée */
      }
    })();
    return () => {
      ok = false;
    };
  }, []);

  useEffect(() => {
    setDraftById((prev) => {
      const next = { ...prev };
      for (const r of requests) {
        if (r.status !== "pending") continue;
        if (!next[r.id]) next[r.id] = defaultDraft();
      }
      return next;
    });
  }, [requests]);

  useEffect(() => {
    return () => {
      if (proofUrl) URL.revokeObjectURL(proofUrl);
    };
  }, [proofUrl]);

  async function openProof(req) {
    if (proofUrl) URL.revokeObjectURL(proofUrl);
    setProofUrl(null);
    setProofTitle(req.original_filename || `preuve #${req.id}`);
    try {
      const res = await fetch(apiUrl(`/api/admin/credit-topups/${req.id}/proof`), {
        headers: getAuthHeaders(false),
      });
      if (!res.ok) throw new Error("Impossible de charger la preuve.");
      const blob = await res.blob();
      setProofUrl(URL.createObjectURL(blob));
    } catch (e) {
      toast(e?.message || "Prévisualisation impossible", "error");
    }
  }

  function closeProof() {
    if (proofUrl) URL.revokeObjectURL(proofUrl);
    setProofUrl(null);
    setProofTitle("");
  }

  /** @param {number} reqId */
  async function approveRequest(reqId) {
    const draft = draftById[reqId] ?? defaultDraft();
    const built = approvePayloadFromDraft(draft);
    if (!built.ok) {
      toast(built.error, "error");
      return;
    }
    const payload = { ...built.payload, admin_note: draft.approveNote.trim() || null };

    setBusyRowId(reqId);
    try {
      const res = await fetch(apiUrl(`/api/admin/credit-topups/${reqId}/approve`), {
        method: "POST",
        headers: { ...getAuthHeaders(false), "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage || "Validation refusée.");
      const msg =
        typeof p.data?.mru_credited_approx === "number"
          ? `Solde utilisateur augmenté (~${Number(p.data.mru_credited_approx).toPrecision(8)} MRU).`
          : "Demande validée.";
      toast(msg, "success");
      reload();
    } catch (e) {
      toast(e?.message || "Erreur", "error");
    } finally {
      setBusyRowId(null);
    }
  }

  async function grantManualCredit() {
    if (!manualSelectedUser) return;
    const built = approvePayloadFromDraft(manualDraft, { allowMruDebit: true });
    if (!built.ok) {
      toast(built.error, "error");
      return;
    }
    setManualBusy(true);
    try {
      const res = await fetch(apiUrl(`/api/admin/users/${manualSelectedUser.id}/grant-wallet`), {
        method: "POST",
        headers: { ...getAuthHeaders(false), "Content-Type": "application/json" },
        body: JSON.stringify(built.payload),
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage || "Crédit impossible.");
      const mruApprox = typeof p.data?.mru_credited_approx === "number" ? Number(p.data.mru_credited_approx) : null;
      let msg = "Portefeuille mis à jour.";
      if (mruApprox != null) {
        const s = Number(mruApprox.toPrecision(8));
        if (s > 0) msg = `Portefeuille crédité (~${s} MRU ajoutés).`;
        else if (s < 0) msg = `Portefeuille débité (~${Number(Math.abs(s).toPrecision(8))} MRU retirés).`;
      }
      toast(msg, "success");
      if (manualSelectedUser && p.data?.user_id === manualSelectedUser.id) {
        setManualSelectedUser((prev) =>
          prev
            ? {
                ...prev,
                credit_balance: typeof p.data.credit_balance === "number" ? p.data.credit_balance : prev.credit_balance,
                balance_mru_approx:
                  typeof p.data.balance_mru_approx === "number" ? p.data.balance_mru_approx : prev.balance_mru_approx,
              }
            : prev
        );
      }
    } catch (e) {
      toast(e?.message || "Erreur", "error");
    } finally {
      setManualBusy(false);
    }
  }

  /** @param {number} reqId */
  async function rejectRequest(reqId) {
    const draft = draftById[reqId] ?? defaultDraft();
    if (!window.confirm("Refuser cette demande sans créditer de MRU au portefeuille ?")) return;
    setBusyRowId(reqId);
    try {
      const res = await fetch(apiUrl(`/api/admin/credit-topups/${reqId}/reject`), {
        method: "POST",
        headers: { ...getAuthHeaders(false), "Content-Type": "application/json" },
        body: JSON.stringify({ admin_note: draft.rejectNote.trim() || null }),
      });
      const p = await parseJsonResponse(res);
      if (!p.ok) throw new Error(p.errorMessage || "Refus impossible.");
      toast("Demande refusée.", "success");
      reload();
    } catch (e) {
      toast(e?.message || "Erreur", "error");
    } finally {
      setBusyRowId(null);
    }
  }

  return (
    <div className="mx-auto w-full min-w-0 max-w-4xl space-y-10 overflow-x-clip pb-24">
      <div className="flex w-full min-w-0 flex-col gap-5">
        <div className="min-w-0 w-full sm:min-w-[12rem] sm:max-w-[min(100%,28rem)] sm:w-auto">
          {typeof onBack === "function" ? (
            <button
              type="button"
              onClick={onBack}
              className="mb-2 text-xs font-semibold text-brand-600 hover:underline dark:text-brand-400"
            >
              ← Retour
            </button>
          ) : null}
          <h1 className="max-w-full break-words font-display text-2xl font-bold tracking-tight text-slate-900 [overflow-wrap:anywhere] dark:text-white md:text-[1.65rem]">
            {adminSection === "users" ? "Comptes utilisateurs" : "Validation des recharges"}
          </h1>
          <p className="mt-2 max-w-full text-[13px] leading-relaxed text-slate-600 [overflow-wrap:anywhere] break-words dark:text-slate-400">
            {adminSection === "users" ? (
              <>
                Liste paginée de tous les comptes inscrits. Filtre optionnel par e-mail (≥ 2 caractères). Pour ajuster un
                solde, ouvre la fiche puis passe par <strong className="text-slate-800 dark:text-slate-200">Recharges → Crédit sans demande</strong>.
              </>
            ) : (
              <>
                Le portefeuille utilisateur est en <strong>MRU</strong>. Après virement, soit tu saisis le{" "}
                <strong>côt USD (fournisseur / API)</strong> prévu pour la recharge : la plateforme créditera automatiquement
                les MRU équivalents (+ marge comme pour une consommation), soit tu passes en mode saisie directe des{" "}
                <strong>MRU</strong>.
              </>
            )}
          </p>
        </div>
        <div className="flex w-full min-w-0 flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
          <div
            role="tablist"
            aria-label="Vue administrateur"
            className="grid w-full min-w-0 grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-none sm:flex-wrap"
          >
            {[
              ["topups", "Recharges"],
              ["users", "Utilisateurs"],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={adminSection === key}
                onClick={() => setAdminSection(key)}
                className={`min-w-0 rounded-full px-3 py-2 text-center text-[10px] font-semibold uppercase tracking-[0.08em] transition sm:text-[11px] sm:tracking-[0.12em] ${
                  adminSection === key
                    ? "bg-emerald-600 text-white shadow-md ring-2 ring-emerald-500/30 ring-offset-0 ring-offset-slate-50 dark:bg-emerald-500 dark:ring-offset-slate-950 sm:px-5 sm:ring-offset-2"
                    : "border border-slate-200 bg-white text-slate-700 shadow-sm hover:border-slate-300 dark:border-slate-600 dark:bg-slate-900/90 dark:text-slate-200 dark:hover:border-slate-500"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {adminSection === "topups" ? (
            <div
              role="tablist"
              aria-label="Filtrer par statut"
              className="grid w-full min-w-0 grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:justify-end"
            >
              {["pending", "all", "approved", "rejected"].map((s) => (
                <button
                  key={s}
                  type="button"
                  role="tab"
                  aria-selected={filter === s}
                  onClick={() => setFilter(s)}
                  className={`min-w-0 rounded-full px-2.5 py-2 text-center text-[10px] font-semibold uppercase tracking-[0.08em] transition sm:min-w-0 sm:px-4 sm:text-[11px] sm:tracking-[0.12em] ${
                    filter === s
                      ? "bg-brand-600 text-white shadow-md ring-2 ring-brand-500/30 ring-offset-0 ring-offset-slate-50 dark:bg-brand-500 dark:ring-offset-slate-950 sm:ring-offset-2"
                      : "border border-slate-200 bg-white text-slate-700 shadow-sm hover:border-slate-300 dark:border-slate-600 dark:bg-slate-900/90 dark:text-slate-200 dark:hover:border-slate-500"
                  }`}
                >
                  {s === "all" ? "Toutes" : STATUS_FR[s] || s}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      {adminSection === "users" ? (
        <section
          aria-labelledby="all-users-heading"
          className="min-w-0 overflow-x-clip rounded-3xl border border-slate-200/90 bg-white/95 p-4 shadow-soft-lg dark:border-slate-700/90 dark:bg-slate-900/95 sm:p-6"
        >
          <h2 id="all-users-heading" className="font-display text-lg font-bold text-slate-900 dark:text-white">
            Tous les utilisateurs
          </h2>
          <div className="mt-4 flex min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
            <div className="min-w-0 sm:max-w-md sm:flex-1">
              <label htmlFor="admin-users-filter" className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                Filtrer par e-mail
              </label>
              <input
                id="admin-users-filter"
                type="search"
                autoComplete="off"
                placeholder="ex. @gmail — laisser vide pour tout afficher"
                value={usersFilterInput}
                onChange={(e) => setUsersFilterInput(e.target.value)}
                className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 dark:border-slate-600 dark:bg-slate-950 dark:text-white"
              />
              {usersFilterInput.trim().length > 0 && usersFilterInput.trim().length < 2 ? (
                <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">Au moins 2 caractères pour filtrer.</p>
              ) : null}
            </div>
            <p className="text-[12px] tabular-nums text-slate-600 dark:text-slate-400">
              {usersTotal} compte{usersTotal !== 1 ? "s" : ""}
              {usersDebouncedFilter.length >= 2 ? " (filtrés)" : ""}
            </p>
          </div>

          {usersLoading ? (
            <div className="flex justify-center py-16">
              <div className="flex flex-col items-center gap-3">
                <div className="size-10 animate-spin rounded-full border-[3px] border-slate-200 border-t-brand-600 dark:border-slate-700 dark:border-t-brand-400" />
                <p className="text-sm text-slate-500 dark:text-slate-400">Chargement…</p>
              </div>
            </div>
          ) : usersRows.length === 0 ? (
            <p className="mt-8 rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 py-12 text-center text-sm text-slate-600 dark:border-slate-600 dark:bg-slate-950/50 dark:text-slate-400">
              Aucun compte pour cette page ou ce filtre.
            </p>
          ) : (
            <div className="mt-6 max-w-full overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-700">
              <table className="w-full min-w-[32rem] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50/90 dark:border-slate-700 dark:bg-slate-950/80">
                    <th className="px-3 py-3 text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                      E-mail
                    </th>
                    <th className="px-3 py-3 text-right text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                      Solde (~MRU)
                    </th>
                    <th className="hidden px-3 py-3 text-[10px] font-bold uppercase tracking-wider text-slate-500 md:table-cell dark:text-slate-400">
                      Inscription
                    </th>
                    <th className="px-3 py-3 text-right text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                      Action
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {usersRows.map((u) => (
                    <tr
                      key={u.id}
                      className="border-b border-slate-100 odd:bg-white even:bg-slate-50/50 last:border-b-0 dark:border-slate-800 dark:odd:bg-slate-900/40 dark:even:bg-slate-900/65"
                    >
                      <td className="max-w-[14rem] px-3 py-2.5 align-top md:max-w-none">
                        <span className="break-all font-medium text-slate-900 dark:text-white">{u.email}</span>
                        {u.is_admin ? (
                          <span className="mt-1 block w-fit rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-950 dark:bg-amber-950/70 dark:text-amber-100">
                            Admin
                          </span>
                        ) : null}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                        {typeof u.balance_mru_approx === "number" ? `${u.balance_mru_approx} MRU` : "—"}
                      </td>
                      <td className="hidden whitespace-nowrap px-3 py-2.5 text-xs text-slate-500 md:table-cell dark:text-slate-400">
                        <time dateTime={u.created_at || undefined}>
                          {u.created_at ? new Date(u.created_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "—"}
                        </time>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <button
                          type="button"
                          onClick={() => {
                            setAdminSection("topups");
                            setManualSelectedUser({
                              id: u.id,
                              email: u.email,
                              is_admin: u.is_admin,
                              balance_mru_approx: u.balance_mru_approx,
                              credit_balance: u.credit_balance,
                            });
                            setManualEmailInput(u.email);
                            setManualSearchHits([]);
                            setManualDraft(defaultDraft());
                          }}
                          className="rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1.5 text-[11px] font-bold text-brand-800 transition hover:bg-brand-100 dark:border-brand-800 dark:bg-brand-950/50 dark:text-brand-200 dark:hover:bg-brand-950/80"
                        >
                          Créditer
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!usersLoading && usersTotal > 0 ? (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4 dark:border-slate-800">
              <p className="text-[11px] text-slate-500 dark:text-slate-400">
                Lignes {usersOffset + 1}–{usersOffset + usersRows.length} sur {usersTotal}
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={usersOffset <= 0}
                  onClick={() => setUsersOffset((o) => Math.max(0, o - USERS_PAGE_SIZE))}
                  className="rounded-full border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
                >
                  Précédent
                </button>
                <button
                  type="button"
                  disabled={usersOffset + usersRows.length >= usersTotal}
                  onClick={() => setUsersOffset((o) => o + USERS_PAGE_SIZE)}
                  className="rounded-full border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
                >
                  Suivant
                </button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {adminSection === "topups" ? (
      <section
        aria-labelledby="manual-grant-heading"
        className="min-w-0 overflow-x-clip rounded-3xl border border-brand-200/90 bg-gradient-to-br from-brand-50/90 via-white to-amber-50/30 p-4 shadow-soft-lg dark:border-brand-900/50 dark:from-brand-950/25 dark:via-slate-900 dark:to-slate-900 sm:p-6"
      >
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-brand-200/70 pb-4 dark:border-brand-800/45">
          <div className="min-w-0">
            <h2 id="manual-grant-heading" className="font-display text-lg font-bold text-slate-900 dark:text-white">
              Crédit sans demande
            </h2>
            <p className="mt-1.5 max-w-full text-[13px] leading-relaxed text-slate-600 [overflow-wrap:anywhere] break-words dark:text-slate-400 sm:max-w-2xl">
              Cherche un compte par e-mail (minimum 2 caractères dans l’adresse), sélectionne-le, puis utilise la même logique{" "}
              <strong className="font-semibold">USD → MRU (+ marge)</strong> ou <strong>MRU directs</strong> que pour une demande avec preuve (sans passer par une demande utilisateur).
            </p>
          </div>
        </div>

        <div className="mt-4 grid min-w-0 gap-4 sm:grid-cols-5">
          <div className="min-w-0 space-y-2 sm:col-span-2">
            <label htmlFor="manual-email-search" className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
              Rechercher par e-mail
            </label>
            <input
              id="manual-email-search"
              type="text"
              autoComplete="off"
              placeholder="ex. prenom@…"
              value={manualEmailInput}
              disabled={manualBusy}
              onChange={(e) => setManualEmailInput(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 dark:text-white"
            />
            {manualSearchLoading ? (
              <p className="text-[11px] text-slate-500 dark:text-slate-400">Recherche…</p>
            ) : manualEmailInput.trim().length > 0 && manualEmailInput.trim().length < 2 ? (
              <p className="text-[11px] text-slate-500 dark:text-slate-400">Saisir au moins 2 caractères.</p>
            ) : null}
            {!manualSelectedUser &&
            manualEmailInput.trim().length >= 2 &&
            !manualSearchLoading &&
            manualSearchHits.length === 0 ? (
              <p className="text-[11px] text-slate-600 dark:text-slate-400">Aucun compte ne correspond.</p>
            ) : null}
            {!manualSelectedUser && manualSearchHits.length ? (
              <ul
                className="max-h-48 overflow-auto rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-600 dark:bg-slate-950"
                role="listbox"
                aria-label="Résultats de recherche"
              >
                {manualSearchHits.map((u) => (
                  <li key={u.id}>
                    <button
                      type="button"
                      disabled={manualBusy}
                      onClick={() => {
                        setManualSelectedUser(u);
                        setManualSearchHits([]);
                      }}
                      className="flex w-full flex-col items-start gap-0.5 border-b border-slate-100 px-3 py-2.5 text-left text-xs last:border-b-0 hover:bg-brand-50/80 disabled:opacity-45 dark:border-slate-800 dark:hover:bg-brand-950/40"
                    >
                      <span className="min-w-0 break-all font-semibold text-slate-900 dark:text-white">{u.email}</span>
                      <span className="tabular-nums text-slate-500 dark:text-slate-400">
                        Solde ≈{" "}
                        {typeof u.balance_mru_approx === "number" ? `${u.balance_mru_approx} MRU` : "—"}
                      </span>
                      {u.is_admin ? (
                        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-950 dark:bg-amber-950/70 dark:text-amber-100">
                          Rôle admin
                        </span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="min-w-0 sm:col-span-3">
            {manualSelectedUser ? (
              <>
                <div className="flex flex-col gap-2 rounded-2xl border border-emerald-200/90 bg-emerald-50/50 px-3 py-2.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between dark:border-emerald-900/55 dark:bg-emerald-950/25">
                  <div className="min-w-0">
                    <p className="text-[10px] font-bold uppercase tracking-wider text-emerald-800 dark:text-emerald-300">Compte sélectionné</p>
                    <p className="break-words text-sm font-bold text-slate-900 dark:text-white sm:truncate">{manualSelectedUser.email}</p>
                    <p className="text-[11px] text-slate-600 dark:text-slate-400">
                      Solde actuel&nbsp;≈{" "}
                      {typeof manualSelectedUser.balance_mru_approx === "number"
                        ? `${manualSelectedUser.balance_mru_approx} MRU`
                        : "—"}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={manualBusy}
                    onClick={() => {
                      setManualSelectedUser(null);
                      setManualDraft(defaultDraft());
                    }}
                    className="shrink-0 rounded-xl border border-slate-300 bg-white px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                  >
                    Changer
                  </button>
                </div>

                <div className="mt-4 space-y-4">
                  <div className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-slate-50/80 p-3 sm:flex-row sm:flex-wrap sm:gap-3 dark:border-slate-600 dark:bg-slate-950/80">
                    {["usd", "mru"].map((m) => (
                      <label
                        key={m}
                        className={`flex min-w-0 cursor-pointer items-start gap-2 rounded-xl px-3 py-2 text-xs font-semibold leading-snug sm:items-center ${
                          manualDraft.grantMode === m
                            ? "bg-brand-600 text-white shadow-inner dark:bg-brand-500"
                            : "border border-transparent text-slate-600 hover:bg-white dark:text-slate-300 dark:hover:bg-slate-800"
                        }`}
                      >
                        <input
                          type="radio"
                          className="sr-only"
                          checked={manualDraft.grantMode === m}
                          disabled={manualBusy}
                          onChange={() => patchManualDraft({ grantMode: m })}
                        />
                        {m === "usd" ? "Depuis coût fournisseur (USD)" : "Ajustement MRU (solde ±)"}
                      </label>
                    ))}
                  </div>

                  {manualDraft.grantMode === "usd" ? (
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div>
                        <label htmlFor="manual-usd" className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                          Coût USD (fournisseur, sans marge client)<span className="text-rose-600 dark:text-rose-400">*</span>
                        </label>
                        <input
                          id="manual-usd"
                          type="text"
                          inputMode="decimal"
                          disabled={manualBusy}
                          placeholder="ex. 0.00001"
                          value={manualDraft.supplierUsd}
                          onChange={(e) => patchManualDraft({ supplierUsd: e.target.value })}
                          className="mt-1 w-full max-w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 font-mono text-sm tabular-nums outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 dark:text-white sm:max-w-[14rem]"
                        />
                        {pricingParams && (() => {
                          const ux = Number(String(manualDraft.supplierUsd || "").replace(",", "."));
                          if (!Number.isFinite(ux) || ux <= 0) return null;
                          const billed = ux * pricingParams.mruPerUsd * pricingParams.margin;
                          const s = billed >= 1 ? billed.toFixed(4) : billed.toPrecision(5);
                          return (
                            <p className="mt-2 text-[11px] leading-relaxed text-emerald-800 dark:text-emerald-300">
                              <strong>{s}</strong> MRU environ (arrondis portefeuille) — {pricingParams.mruPerUsd} MRU/USD, ×{pricingParams.margin}.
                            </p>
                          );
                        })()}
                      </div>
                      <div>
                        <label htmlFor="manual-days-usd" className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                          Prolonger la validité (jours)
                        </label>
                        <input
                          id="manual-days-usd"
                          type="number"
                          inputMode="numeric"
                          min={1}
                          max={3650}
                          placeholder="vide = défaut serveur"
                          disabled={manualBusy}
                          value={manualDraft.extendDays}
                          onChange={(e) => patchManualDraft({ extendDays: e.target.value })}
                          className="mt-1 w-full max-w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm tabular-nums outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 sm:max-w-[12rem]"
                        />
                      </div>
                    </div>
                  ) : (
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div>
                        <label htmlFor="manual-mru" className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                          MRU à créditer (+) ou retirer (−)<span className="text-rose-600 dark:text-rose-400">*</span>
                        </label>
                        <input
                          id="manual-mru"
                          type="text"
                          inputMode="decimal"
                          disabled={manualBusy}
                          placeholder="ex. 120 ou -50"
                          value={manualDraft.directMru}
                          onChange={(e) => patchManualDraft({ directMru: e.target.value })}
                          className="mt-1 w-full max-w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 font-mono text-sm tabular-nums outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 sm:max-w-[14rem]"
                        />
                        <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
                          Nombre <strong>positif</strong> : ajoute au solde. <strong>Négatif</strong> (ex. <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">-100</code>) : retire
                          du solde (refusé si le solde est insuffisant). Un retrait ne prolonge pas la date de validité.
                        </p>
                      </div>
                      <div>
                        <label htmlFor="manual-days-mru" className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                          Prolonger la validité (jours)
                        </label>
                        <input
                          id="manual-days-mru"
                          type="number"
                          inputMode="numeric"
                          min={1}
                          max={3650}
                          placeholder="vide = défaut"
                          disabled={manualBusy}
                          value={manualDraft.extendDays}
                          onChange={(e) => patchManualDraft({ extendDays: e.target.value })}
                          className="mt-1 w-full max-w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm tabular-nums outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 sm:max-w-[12rem]"
                        />
                      </div>
                    </div>
                  )}

                  <button
                    type="button"
                    disabled={manualBusy}
                    onClick={() => void grantManualCredit()}
                    className="w-full rounded-2xl bg-gradient-to-r from-amber-600 to-brand-600 py-3 text-sm font-bold text-white shadow-lg shadow-brand-600/25 transition hover:from-amber-500 hover:to-brand-500 disabled:opacity-45 sm:w-auto sm:min-w-[12rem]"
                  >
                    {manualBusy ? "Application…" : "Appliquer au portefeuille"}
                  </button>
                  <p className="max-w-full text-[11px] leading-relaxed text-slate-500 [overflow-wrap:anywhere] break-words dark:text-slate-400">
                    Impossible de créditer le compte admin avec lequel tu es connecté (sécurité). Déconnecte-toi et passe par un second navigateur ou un autre compte admin pour te créditer toi-même.
                  </p>
                </div>
              </>
            ) : (
              <div className="flex h-full min-h-[120px] items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 px-4 text-center text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-950/50 dark:text-slate-400">
                Choisis d’abord un utilisateur dans la liste à gauche.
              </div>
            )}
          </div>
        </div>
      </section>
      ) : null}

      {adminSection === "topups" ? (
      <>
      <h2 className="font-display text-base font-bold tracking-tight text-slate-800 dark:text-slate-100">
        Demandes avec preuve de virement
      </h2>

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="flex flex-col items-center gap-4">
            <div className="size-11 animate-spin rounded-full border-[3px] border-slate-200 border-t-brand-600 dark:border-slate-700 dark:border-t-brand-400" />
            <p className="text-sm text-slate-500 dark:text-slate-400">Chargement des demandes…</p>
          </div>
        </div>
      ) : requests.length === 0 ? (
        <div className="rounded-3xl border border-dashed border-slate-300/90 bg-white/70 px-6 py-14 text-center text-sm text-slate-600 dark:border-slate-600 dark:bg-slate-900/50 dark:text-slate-400">
          Aucune demande pour ce filtre.
        </div>
      ) : (
        <ul className="space-y-6">
          {requests.map((r) => {
            const draft = draftById[r.id] ?? defaultDraft();
            const isPending = r.status === "pending";
            const rowBusy = busyRowId === r.id;

            return (
              <li
                key={r.id}
                className={`relative min-w-0 overflow-hidden rounded-3xl border shadow-soft-lg transition dark:shadow-none ${
                  isPending
                    ? "border-emerald-200/90 bg-gradient-to-br from-white via-white to-emerald-50/50 dark:border-emerald-900/45 dark:from-slate-900 dark:via-slate-900 dark:to-emerald-950/25"
                    : "border-slate-200/90 bg-white/95 dark:border-slate-700/90 dark:bg-slate-900/95"
                }`}
              >
                {isPending ? (
                  <div
                    aria-hidden="true"
                    className="absolute left-0 top-0 h-1 w-full bg-gradient-to-r from-emerald-500 via-teal-500 to-brand-600 opacity-85"
                  />
                ) : null}

                <div className="min-w-0 p-5 pt-6 sm:p-6">
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500 dark:text-slate-400">
                        Demande #{r.id}
                      </p>
                      <p className="mt-2 break-words font-display text-lg font-bold text-slate-900 [overflow-wrap:anywhere] dark:text-white">
                        {r.user_email || `Utilisateur #${r.user_id}`}
                      </p>
                      <p className="mt-1.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-500 [overflow-wrap:anywhere] break-words dark:text-slate-400">
                        <span
                          className={`inline-flex max-w-full shrink-0 rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
                            r.status === "pending"
                              ? "bg-amber-100 text-amber-900 dark:bg-amber-950/80 dark:text-amber-200"
                              : r.status === "approved"
                                ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/70 dark:text-emerald-100"
                                : "bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-100"
                          }`}
                        >
                          {STATUS_FR[r.status] || r.status}
                        </span>
                        <span className="shrink-0">·</span>
                        <span className="min-w-0">
                          Envoyée{" "}
                          <time className="break-all" dateTime={r.created_at}>
                            {r.created_at ? new Date(r.created_at).toLocaleString("fr-FR") : "—"}
                          </time>
                        </span>
                        {r.reviewed_at ? (
                          <>
                            <span className="shrink-0">·</span>
                            <span className="min-w-0">
                              Traitée{" "}
                              <time className="break-all" dateTime={r.reviewed_at}>
                                {new Date(r.reviewed_at).toLocaleString("fr-FR")}
                              </time>
                            </span>
                          </>
                        ) : null}
                      </p>
                    </div>
                    <div className="flex w-full min-w-0 flex-shrink-0 flex-wrap gap-2 sm:w-auto sm:justify-end">
                      <button
                        type="button"
                        disabled={rowBusy}
                        onClick={() => openProof(r)}
                        className="w-full rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-800 shadow-sm transition hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700 sm:w-auto"
                      >
                        Voir la preuve
                      </button>
                    </div>
                  </div>

                  {isPending ? (
                    <div className="mt-5 space-y-4 border-t border-slate-200/80 pt-5 dark:border-slate-700/80">
                      <p className="max-w-full text-[11px] font-bold uppercase tracking-[0.12em] text-slate-500 [overflow-wrap:anywhere] break-words dark:text-slate-400 sm:tracking-[0.15em]">
                        Méthode — coût fournisseur (USD→MRU + marge) ou MRU saisis au portefeuille
                      </p>
                      <div className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-slate-50/80 p-3 sm:flex-row sm:flex-wrap sm:gap-3 dark:border-slate-600 dark:bg-slate-950/80">
                        {["usd", "mru"].map((m) => (
                          <label
                            key={m}
                            className={`flex min-w-0 cursor-pointer items-start gap-2 rounded-xl px-3 py-2 text-xs font-semibold leading-snug sm:items-center ${
                              draft.grantMode === m
                                ? "bg-brand-600 text-white shadow-inner dark:bg-brand-500"
                                : "border border-transparent text-slate-600 hover:bg-white dark:text-slate-300 dark:hover:bg-slate-800"
                            }`}
                          >
                            <input
                              type="radio"
                              className="sr-only"
                              checked={draft.grantMode === m}
                              disabled={rowBusy}
                              onChange={() => patchDraft(r.id, { grantMode: m })}
                            />
                            {m === "usd" ? "Depuis coût fournisseur (USD)" : "Montant MRU au portefeuille"}
                          </label>
                        ))}
                      </div>

                      {draft.grantMode === "usd" ? (
                        <div className="grid gap-4 sm:grid-cols-2">
                          <div>
                            <label
                              htmlFor={`usd-${r.id}`}
                              className="block max-w-full text-xs font-semibold leading-snug text-slate-700 [overflow-wrap:anywhere] break-words dark:text-slate-300"
                            >
                              Coût USD aux APIs / fournisseur (sans marge client)<span className="text-rose-600 dark:text-rose-400">*</span>
                            </label>
                            <input
                              id={`usd-${r.id}`}
                              type="text"
                              inputMode="decimal"
                              disabled={rowBusy}
                              placeholder="ex. 0.00001"
                              value={draft.supplierUsd}
                              onChange={(e) => patchDraft(r.id, { supplierUsd: e.target.value })}
                              className="mt-1 w-full max-w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 font-mono text-sm tabular-nums text-slate-900 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 dark:text-white sm:max-w-[14rem]"
                            />
                            {pricingParams && (() => {
                              const ux = Number(String(draft.supplierUsd || "").replace(",", "."));
                              if (!Number.isFinite(ux) || ux <= 0) return null;
                              const billed = ux * pricingParams.mruPerUsd * pricingParams.margin;
                              const s = billed >= 1 ? billed.toFixed(4) : billed.toPrecision(5);
                              return (
                                <p className="mt-2 max-w-full text-[11px] leading-relaxed text-emerald-800 [overflow-wrap:anywhere] break-words dark:text-emerald-300">
                                  <strong>{s}</strong> MRU seront environ crédités (arrondis portefeuille après) avec{" "}
                                  {pricingParams.mruPerUsd} MRU/USD et ×{pricingParams.margin} marge.
                                </p>
                              );
                            })()}
                          </div>
                          <div>
                            <label htmlFor={`days-${r.id}`} className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                              Prolonger la validité (jours)
                            </label>
                            <input
                              id={`days-${r.id}`}
                              type="number"
                              inputMode="numeric"
                              min={1}
                              max={3650}
                              placeholder="vide = défaut serveur"
                              disabled={rowBusy}
                              value={draft.extendDays}
                              onChange={(e) => patchDraft(r.id, { extendDays: e.target.value })}
                              className="mt-1 w-full max-w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm tabular-nums outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 sm:max-w-[12rem]"
                            />
                          </div>
                        </div>
                      ) : (
                        <div className="grid gap-4 sm:grid-cols-2">
                          <div>
                            <label htmlFor={`mru-${r.id}`} className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                              MRU ajoutés au portefeuille client<span className="text-rose-600 dark:text-rose-400">*</span>
                            </label>
                            <input
                              id={`mru-${r.id}`}
                              type="text"
                              inputMode="decimal"
                              disabled={rowBusy}
                              placeholder="ex. 0.05"
                              value={draft.directMru}
                              onChange={(e) => patchDraft(r.id, { directMru: e.target.value })}
                              className="mt-1 w-full max-w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 font-mono text-sm tabular-nums outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 sm:max-w-[14rem]"
                            />
                          </div>
                          <div>
                            <label htmlFor={`days2-${r.id}`} className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                              Prolonger la validité (jours)
                            </label>
                            <input
                              id={`days2-${r.id}`}
                              type="number"
                              inputMode="numeric"
                              min={1}
                              max={3650}
                              placeholder="vide = défaut"
                              disabled={rowBusy}
                              value={draft.extendDays}
                              onChange={(e) => patchDraft(r.id, { extendDays: e.target.value })}
                              className="mt-1 w-full max-w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm tabular-nums outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 sm:max-w-[12rem]"
                            />
                          </div>
                        </div>
                      )}

                      <div>
                        <label htmlFor={`apnote-${r.id}`} className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                          Note après validation (optionnel)
                        </label>
                        <textarea
                          id={`apnote-${r.id}`}
                          rows={2}
                          disabled={rowBusy}
                          value={draft.approveNote}
                          onChange={(e) => patchDraft(r.id, { approveNote: e.target.value })}
                          placeholder="Ex. Virement bien reçu…"
                          className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-500/30 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 dark:text-slate-100"
                        />
                      </div>

                      <div>
                        <label htmlFor={`rejnote-${r.id}`} className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                          Note si refus (optionnel)
                        </label>
                        <textarea
                          id={`rejnote-${r.id}`}
                          rows={2}
                          disabled={rowBusy}
                          value={draft.rejectNote}
                          onChange={(e) => patchDraft(r.id, { rejectNote: e.target.value })}
                          className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400 focus:ring-2 focus:ring-slate-400/20 disabled:opacity-45 dark:border-slate-600 dark:bg-slate-950 dark:text-slate-100"
                        />
                      </div>

                      <div className="flex flex-col gap-3 pt-1 sm:flex-row sm:flex-wrap sm:items-stretch">
                        <button
                          type="button"
                          disabled={rowBusy}
                          onClick={() => void approveRequest(r.id)}
                          className="inline-flex min-h-[2.75rem] w-full min-w-0 flex-1 items-center justify-center whitespace-normal rounded-2xl bg-gradient-to-r from-emerald-600 to-teal-600 px-4 py-2.5 text-center text-sm font-bold leading-snug text-white shadow-lg shadow-emerald-600/20 transition hover:from-emerald-500 hover:to-teal-500 disabled:opacity-45 sm:min-w-[10rem] sm:w-auto sm:flex-none sm:px-5"
                        >
                          {rowBusy ? "Patienter…" : draft.grantMode === "usd" ? "Approuver (USD MRU+marge)" : `Approuver +${draft.directMru || "…"} MRU`}
                        </button>
                        <button
                          type="button"
                          disabled={rowBusy}
                          onClick={() => void rejectRequest(r.id)}
                          className="inline-flex w-full min-w-0 items-center justify-center whitespace-normal rounded-2xl border-2 border-rose-400/70 bg-rose-500/[0.08] px-4 py-2.5 text-center text-sm font-bold leading-snug text-rose-800 transition hover:bg-rose-500/15 disabled:opacity-45 dark:border-rose-800 dark:bg-rose-950/45 dark:text-rose-200 sm:w-auto sm:px-5"
                        >
                          Refuser la demande
                        </button>
                      </div>
                    </div>
                  ) : null}

                  {r.status === "approved" && typeof r.granted_mru_approx === "number" ? (
                    <div className="mt-4 max-w-full border-t border-slate-100 pt-4 text-[12px] text-slate-600 [overflow-wrap:anywhere] break-words dark:border-slate-800 dark:text-slate-400">
                      <span className="font-semibold text-slate-800 dark:text-slate-100">MRU crédités au portefeuille (approx.) :</span>{" "}
                      {r.granted_mru_approx} MRU
                    </div>
                  ) : null}
                  {r.admin_note ? (
                    <p className="mt-2 max-w-full text-[12px] italic leading-relaxed text-slate-600 [overflow-wrap:anywhere] break-all dark:text-slate-400">
                      Note : {r.admin_note}
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      </>
      ) : null}

      {proofUrl ? (
        <div
          className="fixed inset-0 z-[120] flex items-center justify-center bg-black/65 p-4 backdrop-blur-[2px]"
          role="dialog"
          aria-modal="true"
          aria-labelledby="proof-dialog-title"
        >
          <div className="max-h-[92vh] w-full max-w-3xl overflow-hidden rounded-[1.35rem] border border-white/15 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
            <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-3 dark:border-slate-700">
              <p id="proof-dialog-title" className="truncate text-sm font-bold text-slate-900 dark:text-white">
                {proofTitle}
              </p>
              <button
                type="button"
                onClick={closeProof}
                className="shrink-0 rounded-xl bg-slate-900 px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-white dark:bg-slate-100 dark:text-slate-900"
              >
                Fermer
              </button>
            </div>
            <div className="max-h-[calc(92vh-3.75rem)] overflow-auto bg-slate-100 p-4 dark:bg-slate-950">
              <img
                src={proofUrl}
                alt="Pièce jointe envoyée comme preuve de virement"
                className="mx-auto max-h-[75vh] w-auto max-w-full rounded-xl shadow-lg ring-1 ring-black/5"
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
