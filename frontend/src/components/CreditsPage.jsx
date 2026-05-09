import { useCallback, useEffect, useState } from "react";
import { ENGINE_COURSE, ENGINE_TRANSCRIPTION } from "../branding.js";
import { apiUrl, getAuthHeaders, parseJsonResponse } from "../utils/api.js";

function toast(msg, type) {
  window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg, type } }));
}

const STATUS_FR = {
  pending: "En attente de validation",
  approved: "Validée — crédits ajoutés",
  rejected: "Refusée",
};

/** @typedef {undefined | null | Record<string, unknown>} MeShape */

export default function CreditsPage({ onBack, onWalletUpdated }) {
  const [loading, setLoading] = useState(true);
  /** @type {[MeShape, (v: MeShape) => void]} */
  const [me, setMe] = useState(undefined);
  const [requests, setRequests] = useState([]);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const rm = await fetch(apiUrl("/api/credits/me"), { headers: getAuthHeaders(false) });
      const pm = await parseJsonResponse(rm);
      if (!pm.ok) throw new Error(pm.errorMessage || "Solde inaccessible.");
      setMe(pm.data);

      const rr = await fetch(apiUrl("/api/credits/topup-requests/mine"), { headers: getAuthHeaders(false) });
      const pr = await parseJsonResponse(rr);
      if (pr.ok) setRequests(pr.data.requests || []);
      else setRequests([]);
    } catch (e) {
      toast(e?.message || "Erreur de chargement", "error");
      setMe(null);
      setRequests([]);
    } finally {
      setLoading(false);
      onWalletUpdated?.();
    }
  }, [onWalletUpdated]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function submitProof(e) {
    e.preventDefault();
    if (!file) {
      toast("Choisis une image de ta preuve de virement.", "error");
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
      toast(typeof p.data?.message === "string" ? p.data.message : "Demande envoyée.", "success");
      setFile(null);
      reload();
    } catch (err) {
      toast(err.message || "Envoi impossible", "error");
    } finally {
      setBusy(false);
    }
  }

  /* Premier chargement seulement : évite de masquer tout l’écran si reload se rejoue (anciennes références instables ou refresh). */
  if (me === undefined && loading) {
    return (
      <div className="mx-auto max-w-lg py-16 text-center text-sm text-slate-600 dark:text-slate-400">
        Chargement du portefeuille…
      </div>
    );
  }

  if (me === null) {
    return (
      <div className="mx-auto max-w-xl space-y-4 py-8">
        <button type="button" onClick={onBack} className="text-sm font-semibold text-brand-700 dark:text-brand-300">
          ← Retour
        </button>
        <p className="text-sm text-slate-600 dark:text-slate-400">Impossible d&apos;afficher les crédits.</p>
        <button
          type="button"
          onClick={() => reload()}
          className="rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white"
        >
          Réessayer
        </button>
      </div>
    );
  }

  if (me.feature_enabled === false) {
    return (
      <div className="mx-auto max-w-xl space-y-4 py-8">
        <button type="button" onClick={onBack} className="text-sm font-semibold text-brand-700 dark:text-brand-300">
          ← Retour
        </button>
        <div className="glass-panel rounded-2xl p-5 text-sm text-slate-600 dark:text-slate-400">
          {typeof me.message === "string" ? me.message : "Crédits non actifs sur ce serveur."}
        </div>
      </div>
    );
  }

  const exp = me.credits_expire_at ? new Date(String(me.credits_expire_at)) : null;
  const expStr =
    exp && !Number.isNaN(exp.getTime())
      ? exp.toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" })
      : null;

  return (
    <div className="mx-auto max-w-xl space-y-8 pb-16">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" onClick={onBack} className="text-sm font-semibold text-brand-700 dark:text-brand-300">
          ← Retour
        </button>
      </div>

      <div className="glass-panel rounded-3xl border border-brand-100/80 p-6 shadow-soft dark:border-brand-950/60">
        <h1 className="font-display text-2xl font-bold text-slate-900 dark:text-white">Portefeuille (MRU)</h1>
        <p className="mt-1 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
          Ton solde est en <strong className="text-slate-600 dark:text-slate-300">MRU ouguiya</strong> disponible pour payer{" "}
          {ENGINE_TRANSCRIPTION}, {ENGINE_COURSE} et les exports : chaque action débite automatiquement le MRU selon les coûts
          réels APIs (avec les mêmes conversions que configurées serveur — USD→MRU, marge&nbsp;×3 par défaut). Après ton
          virement, envoie une capture&nbsp;: l’admin te crédite en MRU.
        </p>
        <div className="mt-6 flex flex-wrap items-end gap-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              Solde MRU
            </div>
            <div className="font-display text-4xl font-black tabular-nums text-brand-600 dark:text-brand-400">
              {typeof me.balance_mru === "number"
                ? me.balance_mru
                : typeof me.credit_balance === "number"
                  ? me.credit_balance
                  : "—"}
            </div>
          </div>
          <div className="min-w-[12rem] flex-1 rounded-2xl bg-slate-50 px-4 py-2 text-[11px] leading-relaxed text-slate-600 dark:bg-slate-900/70 dark:text-slate-400">
            <strong className="block text-slate-800 dark:text-slate-200">Validité</strong>
            {expStr ?? "Pas de date d’expiration stockée pour l’instant — demande précision à ton administrateur si besoin."}
            {me.can_use_features === false ? (
              <span className="mt-2 block font-semibold text-amber-800 dark:text-amber-300">
                {String(me.block_reason || "Usage bloqué.")}
              </span>
            ) : (
              <span className="mt-2 block font-medium text-emerald-700 dark:text-emerald-400">Tu peux utiliser l’application</span>
            )}
          </div>
        </div>
      </div>

      <form onSubmit={submitProof} className="glass-panel space-y-4 rounded-3xl p-6 shadow-soft dark:!bg-slate-900/60">
        <h2 className="font-semibold text-slate-900 dark:text-white">Demander une recharge</h2>
        <p className="text-[12px] leading-relaxed text-slate-600 dark:text-slate-400">
          Formats&nbsp;: PNG, JPG, WEBP ou GIF · max&nbsp;6&nbsp;Mo.
        </p>
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif,.png,.jpg,.jpeg,.webp,.gif"
          className="w-full rounded-xl border border-slate-200/80 bg-white/70 px-2 py-2 text-xs dark:border-slate-700 dark:bg-slate-950/60"
          disabled={busy}
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <button
          type="submit"
          disabled={busy || !file}
          className="inline-flex rounded-2xl bg-gradient-to-r from-brand-600 to-violet-600 px-6 py-2.5 text-sm font-bold text-white shadow-glow disabled:cursor-not-allowed disabled:opacity-45"
        >
          Envoyer la capture
        </button>
      </form>

      <div className="space-y-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          Mes demandes récentes
        </h3>
        <ul className="space-y-2">
          {requests.length === 0 ? (
            <li className="text-sm text-slate-500 dark:text-slate-500">Aucune demande enregistrée.</li>
          ) : (
            requests.map((r) => (
              <li key={r.id} className="glass-panel rounded-2xl px-4 py-3 text-xs shadow-sm dark:!bg-slate-900/60">
                <div className="flex justify-between gap-2 font-semibold text-slate-800 dark:text-slate-100">
                  <span>#{r.id}</span>
                  <span>{STATUS_FR[r.status] || r.status}</span>
                </div>
                {r.created_at ? (
                  <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                    {new Date(String(r.created_at)).toLocaleString("fr-FR")}
                  </div>
                ) : null}
                {typeof r.granted_mru_approx === "number" ? (
                  <div className="mt-1 font-medium text-brand-700 dark:text-brand-300">+{r.granted_mru_approx} MRU</div>
                ) : typeof r.credits_granted === "number" ? (
                  <div className="mt-1 font-medium text-brand-700 dark:text-brand-300">+unités&nbsp;internes {r.credits_granted}</div>
                ) : null}
                {r.admin_note ? (
                  <div className="mt-2 text-[11px] italic leading-relaxed text-slate-600 dark:text-slate-500">
                    Note&nbsp;: {r.admin_note}
                  </div>
                ) : null}
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
