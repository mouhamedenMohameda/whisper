import { useCallback, useEffect, useState } from "react";
import { apiUrl, getAuthHeaders, parseJsonResponse } from "../utils/api.js";

function toast(msg, type) {
  window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg, type } }));
}

/**
 * Carte « Lier mon Telegram » — affiche un bouton qui :
 *   1. Appelle POST /api/telegram/link-token (auth requise) — génère un token jetable côté serveur.
 *   2. Ouvre le deep link `https://t.me/<bot>?start=<token>` dans un nouvel onglet / l'app Telegram.
 *   3. Côté Telegram, le bot reçoit `/start <token>`, vérifie + bind atomiquement (1 chat ↔ 1 compte).
 *
 * Pourquoi ce flow (vs un /lier <numéro> côté bot) :
 *   Le token est produit pour l'user authentifié dans l'app — un attaquant qui taperait le numéro
 *   WhatsApp d'un autre user ne pourrait pas voler son compte.
 *
 * Composant volontairement sans i18n pour rester simple — quelques chaînes FR (extensible).
 */
export default function TelegramLinkCard() {
  const [status, setStatus] = useState(/** @type {"idle"|"loading"|"linked"|"error"} */ ("idle"));
  const [linkedChatId, setLinkedChatId] = useState(null);
  const [pendingLink, setPendingLink] = useState(null); // {deep_link, expires_at}
  const [error, setError] = useState("");

  /**
   * Au montage : on demande un token. Si le serveur répond `already_linked:true`, on bascule en
   * mode "déjà lié" et on n'expose pas de lien. Sinon on stocke le lien pour ouverture sur clic.
   */
  const checkLinkStatus = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      const resp = await fetch(apiUrl("/api/telegram/link-token"), {
        method: "POST",
        headers: getAuthHeaders(false),
      });
      const parsed = await parseJsonResponse(resp);
      if (!parsed.ok) throw new Error(parsed.errorMessage || "Impossible de générer le lien Telegram.");
      const data = parsed.data || {};
      if (data.already_linked) {
        setLinkedChatId(data.chat_id || "");
        setStatus("linked");
        setPendingLink(null);
      } else {
        setPendingLink({ deep_link: data.deep_link, expires_at: data.expires_at });
        setLinkedChatId(null);
        setStatus("idle");
      }
    } catch (e) {
      setError(e?.message || "Erreur réseau.");
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void checkLinkStatus();
  }, [checkLinkStatus]);

  const openTelegram = useCallback(() => {
    if (!pendingLink?.deep_link) return;
    // Telegram Desktop / app mobile interceptent l'URL t.me et basculent dans le client natif.
    // En fallback web (pas de client installé), Telegram ouvre la page de redirection puis WebApp.
    window.open(pendingLink.deep_link, "_blank", "noopener,noreferrer");
  }, [pendingLink]);

  const unlink = useCallback(async () => {
    if (!window.confirm("Délier ce chat Telegram du compte ? Tu pourras re-lier ensuite.")) return;
    try {
      const resp = await fetch(apiUrl("/api/telegram/unlink"), {
        method: "POST",
        headers: getAuthHeaders(false),
      });
      const parsed = await parseJsonResponse(resp);
      if (!parsed.ok) throw new Error(parsed.errorMessage || "Échec.");
      toast("Telegram délié.", "success");
      await checkLinkStatus();
    } catch (e) {
      toast(e?.message || "Erreur réseau.", "error");
    }
  }, [checkLinkStatus]);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        {/* Logo Telegram inline (pas de dépendance) */}
        <svg viewBox="0 0 24 24" className="h-5 w-5 text-sky-500" fill="currentColor" aria-hidden>
          <path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z" />
        </svg>
        <h2 className="text-lg font-semibold text-slate-900">Bot Telegram</h2>
      </div>

      {status === "loading" && (
        <p className="text-sm text-slate-500">Chargement…</p>
      )}

      {status === "error" && (
        <div className="space-y-2">
          <p className="text-sm text-red-600">{error}</p>
          <button
            type="button"
            onClick={() => void checkLinkStatus()}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50"
          >
            Réessayer
          </button>
        </div>
      )}

      {status === "linked" && (
        <div className="space-y-3">
          <p className="text-sm text-slate-700">
            ✅ Ton compte est lié au bot Telegram. Tu peux envoyer tes audios directement à{" "}
            <a
              href="https://t.me/lecturai_bot"
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-sky-600 underline"
            >
              @lecturai_bot
            </a>
            .
          </p>
          {linkedChatId ? (
            <p className="text-xs text-slate-400">Chat lié : <code className="font-mono">{linkedChatId}</code></p>
          ) : null}
          <button
            type="button"
            onClick={() => void unlink()}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          >
            Délier
          </button>
        </div>
      )}

      {status === "idle" && pendingLink && (
        <div className="space-y-3">
          <p className="text-sm text-slate-700">
            Reçois tes fiches PDF aussi sur Telegram. Le lien ouvre @lecturai_bot et lie ton compte
            automatiquement — pas besoin de taper de numéro.
          </p>
          <button
            type="button"
            onClick={openTelegram}
            className="inline-flex items-center gap-2 rounded-lg bg-sky-500 px-4 py-2 text-sm font-medium text-white shadow hover:bg-sky-600"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden>
              <path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z" />
            </svg>
            Lier mon Telegram
          </button>
          <p className="text-xs text-slate-400">
            Lien valide 5 minutes. Si tu n'as pas Telegram installé, il s'ouvrira dans le navigateur.
          </p>
        </div>
      )}
    </div>
  );
}
