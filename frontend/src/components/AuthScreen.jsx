import { useState } from "react";
import { apiUrl, parseJsonResponse } from "../utils/api.js";
import { setAuthSession } from "../utils/authStorage.js";
import WhatsAppSupportButton from "./WhatsAppSupportButton.jsx";

function toast(msg, type = "info") {
  window.dispatchEvent(new CustomEvent("lecturai-toast", { detail: { msg, type } }));
}

/** Contrôles UX avant envoi API (messages clairs avant 422). */
function validateIdentityFields(nniRaw, whatsappRaw) {
  const nni = String(nniRaw || "").replace(/\s/g, "");
  if (!/^\d{10,20}$/.test(nni)) {
    toast("Le NNI doit contenir uniquement des chiffres (10 à 20 caractères), sans lettres.", "error");
    return false;
  }
  const wa = String(whatsappRaw || "").trim();
  if (wa.includes("@")) {
    toast("Le champ WhatsApp attend un numéro de téléphone (ex. +222…), pas une adresse e-mail.", "error");
    return false;
  }
  const waDigits = wa.replace(/\D/g, "");
  if (waDigits.length < 8) {
    toast("Indique un numéro WhatsApp valide avec indicatif pays (au moins 8 chiffres).", "error");
    return false;
  }
  return true;
}

const inputClass =
  "w-full rounded-2xl border border-slate-200/90 bg-white/80 px-4 py-3 text-sm text-slate-900 shadow-sm outline-none ring-brand-500/0 transition focus:border-brand-400 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-100 dark:focus:border-brand-500 dark:focus:ring-brand-400/25";

function Field({ label, hint, ...props }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500 dark:text-slate-400">{label}</span>
      <input className={inputClass} {...props} />
      {hint ? <span className="block text-[11px] leading-snug text-slate-500 dark:text-slate-500">{hint}</span> : null}
    </label>
  );
}

/** `revealed` = mot de passe affiché en clair → icône « masquer » (œil barré). */
function PasswordVisibilityIcon({ revealed }) {
  if (revealed) {
    return (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="size-5" aria-hidden>
        <path d="M3.53 2.47a.75.75 0 00-1.06 1.06l18 18a.75.75 0 101.06-1.06l-1.72-1.72C19.86 17.13 21.28 14.52 22.03 12a10.05 10.05 0 00-4.24-5.28L3.53 2.47zM8.65 7.55L10.5 9.4A3 3 0 0012 15a2.98 2.98 0 001.59-.47l1.7 1.7a5.24 5.24 0 01-7.05-1.19A10.02 10.02 0 014.8 12c.69-1.84 1.9-3.48 3.45-4.66l.4-.29zM12 6.25a5.24 5.24 0 013.86 1.69l-1.11 1.11A3.5 3.5 0 0012 7.75c-.45 0-.88.09-1.28.25L8.9 6.22A5.24 5.24 0 0112 6.25zM6.37 3.67a.75.75 0 11-1.06 1.06l2.3 2.3C5.16 8.25 2.53 10.14 1.97 12a10.05 10.05 0 004.13 5.49l-1.75 1.75a.75.75 0 101.06 1.06l18-18a.75.75 0 10-1.06-1.06l-2.16 2.16z" />
      </svg>
    );
  }
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="size-5" aria-hidden>
      <path d="M12 15a3 3 0 100-6 3 3 0 000 6z" />
      <path
        fillRule="evenodd"
        d="M1.323 11.447C2.811 6.976 7.028 3.75 12.001 3.75c4.97 0 9.185 3.223 10.675 7.69.12.362.12.752 0 1.113-1.487 4.471-5.705 7.697-10.677 7.697-4.97 0-9.186-3.223-10.675-7.69a1.762 1.762 0 010-1.113zM17.25 12a5.25 5.25 0 11-10.5 0 5.25 5.25 0 0110.5 0z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function PasswordField({ label, hint, value, onChange, autoComplete, required, minLength }) {
  const [visible, setVisible] = useState(false);
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500 dark:text-slate-400">{label}</span>
      <div className="relative">
        <input
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          required={required}
          minLength={minLength}
          value={value}
          onChange={onChange}
          className={`${inputClass} pr-12`}
        />
        <button
          type="button"
          className="absolute right-2 top-1/2 flex size-9 -translate-y-1/2 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}
          aria-pressed={visible}
        >
          <PasswordVisibilityIcon revealed={visible} />
        </button>
      </div>
      {hint ? <span className="block text-[11px] leading-snug text-slate-500 dark:text-slate-500">{hint}</span> : null}
    </label>
  );
}

export default function AuthScreen({ onAuthed }) {
  const [tab, setTab] = useState("login");
  const [busy, setBusy] = useState(false);

  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");

  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regPassword2, setRegPassword2] = useState("");
  const [regNni, setRegNni] = useState("");
  const [regWa, setRegWa] = useState("");

  const [rpEmail, setRpEmail] = useState("");
  const [rpNni, setRpNni] = useState("");
  const [rpWa, setRpWa] = useState("");
  const [rpNew, setRpNew] = useState("");
  const [rpNew2, setRpNew2] = useState("");

  async function submitLogin(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await fetch(apiUrl("/api/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: loginEmail.trim(), password: loginPassword }),
      });
      const { ok, data, errorMessage } = await parseJsonResponse(res);
      if (!ok) throw new Error(errorMessage || "Connexion impossible.");
      setAuthSession(data.access_token, data.user);
      onAuthed?.(data);
      toast("Bienvenue !", "success");
    } catch (err) {
      toast(err.message || "Connexion impossible", "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitRegister(e) {
    e.preventDefault();
    if (regPassword !== regPassword2) {
      toast("Les deux mots de passe ne correspondent pas.", "error");
      return;
    }
    if (!validateIdentityFields(regNni, regWa)) return;
    setBusy(true);
    try {
      const res = await fetch(apiUrl("/api/auth/register"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: regEmail.trim(),
          password: regPassword,
          nni: regNni.trim(),
          whatsapp: regWa.trim(),
        }),
      });
      const { ok, data, errorMessage } = await parseJsonResponse(res);
      if (!ok) throw new Error(errorMessage || "Création de compte refusée.");
      setAuthSession(data.access_token, data.user);
      onAuthed?.(data);
      toast("Compte créé — tu es connecté.", "success");
    } catch (err) {
      toast(err.message || "Création de compte refusée", "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitReset(e) {
    e.preventDefault();
    if (rpNew !== rpNew2) {
      toast("Les deux mots de passe ne correspondent pas.", "error");
      return;
    }
    if (!validateIdentityFields(rpNni, rpWa)) return;
    setBusy(true);
    try {
      const res = await fetch(apiUrl("/api/auth/reset-password"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: rpEmail.trim(),
          nni: rpNni.trim(),
          whatsapp: rpWa.trim(),
          new_password: rpNew,
        }),
      });
      const { ok, errorMessage } = await parseJsonResponse(res);
      if (!ok) throw new Error(errorMessage || "Réinitialisation impossible.");
      toast("Mot de passe mis à jour. Connecte-toi avec ton nouveau mot de passe.", "success");
      setTab("login");
      setLoginEmail(rpEmail.trim());
      setRpNew("");
      setRpNew2("");
      setRpNni("");
      setRpWa("");
      setLoginPassword("");
    } catch (err) {
      toast(err.message || "Réinitialisation refusée — vérifie e-mail, NNI et WhatsApp.", "error");
    } finally {
      setBusy(false);
    }
  }

  const tabBtn = (id, label) => (
    <button
      type="button"
      onClick={() => setTab(id)}
      className={`rounded-full px-4 py-2 text-xs font-bold transition ${
        tab === id
          ? "bg-gradient-to-r from-brand-600 to-violet-600 text-white shadow-glow"
          : "text-slate-600 hover:bg-white/70 dark:text-slate-400 dark:hover:bg-slate-800/80"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="relative mx-auto max-w-lg px-4 py-16 lg:py-24">
      <div className="glass-panel rounded-[2rem] border border-white/60 p-8 shadow-soft-lg dark:border-slate-800/90 dark:!bg-slate-900/75">
        <div className="text-center">
          <h1 className="font-display text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
            Accès <span className="text-gradient-brand">LecturAI</span>
          </h1>
          <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
            Identifie-toi pour transcrire et générer des cours sur ce serveur.
          </p>
        </div>

        <div className="mt-6">
          <WhatsAppSupportButton variant="auth" />
        </div>

        <div className="mx-auto mt-8 flex flex-wrap justify-center gap-2 rounded-full border border-slate-200/80 bg-slate-50/90 p-1.5 dark:border-slate-700 dark:bg-slate-950/60">
          {tabBtn("login", "Connexion")}
          {tabBtn("register", "Créer un compte")}
          {tabBtn("reset", "Mot de passe oublié")}
        </div>

        {tab === "login" && (
          <form onSubmit={submitLogin} className="mt-8 space-y-4">
            <Field label="E-mail" type="email" autoComplete="email" value={loginEmail} onChange={(e) => setLoginEmail(e.target.value)} required />
            <PasswordField
              label="Mot de passe"
              autoComplete="current-password"
              value={loginPassword}
              onChange={(e) => setLoginPassword(e.target.value)}
              required
            />
            <button
              type="submit"
              disabled={busy}
              className="mt-2 w-full rounded-2xl bg-gradient-to-r from-brand-600 via-brand-500 to-violet-600 py-3.5 text-sm font-bold text-white shadow-glow transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Se connecter
            </button>
          </form>
        )}

        {tab === "register" && (
          <form onSubmit={submitRegister} className="mt-8 space-y-4">
            <Field label="E-mail" type="email" autoComplete="email" value={regEmail} onChange={(e) => setRegEmail(e.target.value)} required />
            <Field
              label="NNI"
              autoComplete="off"
              inputMode="numeric"
              value={regNni}
              onChange={(e) => setRegNni(e.target.value)}
              required
              hint="Numéro national d’identification (chiffres uniquement)."
            />
            <Field
              label="WhatsApp"
              autoComplete="tel"
              placeholder="+222 ..."
              value={regWa}
              onChange={(e) => setRegWa(e.target.value)}
              required
              hint="Ton numéro tel qu’enregistré : il sera redemandé tel quel si tu dois changer ton mot de passe."
            />
            <PasswordField
              label="Mot de passe (min. 8 caractères)"
              autoComplete="new-password"
              value={regPassword}
              onChange={(e) => setRegPassword(e.target.value)}
              required
              minLength={8}
            />
            <PasswordField
              label="Confirmer le mot de passe"
              autoComplete="new-password"
              value={regPassword2}
              onChange={(e) => setRegPassword2(e.target.value)}
              required
              minLength={8}
            />
            <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-500">
              Pour un nouveau mot de passe plus tard : tu réécriras ton e-mail, ton NNI et ce numéro WhatsApp — aucun message automatique n’est envoyé.
            </p>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-2xl bg-gradient-to-r from-brand-600 via-brand-500 to-violet-600 py-3.5 text-sm font-bold text-white shadow-glow transition hover:brightness-105 disabled:opacity-50"
            >
              Créer mon compte
            </button>
          </form>
        )}

        {tab === "reset" && (
          <div className="mt-8 space-y-4">
            <p className="text-center text-xs leading-relaxed text-slate-600 dark:text-slate-400">
              Saisis exactement les mêmes <strong className="text-slate-800 dark:text-slate-200">e-mail</strong>,{" "}
              <strong className="text-slate-800 dark:text-slate-200">NNI</strong> et{" "}
              <strong className="text-slate-800 dark:text-slate-200">numéro WhatsApp</strong> que lors de l&apos;inscription,
              puis ton nouveau mot de passe. Aucun message automatique : on vérifie ton identité uniquement avec ces trois
              éléments.
            </p>
            <form onSubmit={submitReset} className="space-y-3">
              <Field label="E-mail du compte" type="email" autoComplete="email" value={rpEmail} onChange={(e) => setRpEmail(e.target.value)} required />
              <Field label="NNI (re-saisie)" inputMode="numeric" autoComplete="off" value={rpNni} onChange={(e) => setRpNni(e.target.value)} required hint="Les mêmes chiffres qu’à l’inscription." />
              <Field
                label="WhatsApp (re-saisie)"
                placeholder="+222 …"
                autoComplete="tel"
                value={rpWa}
                onChange={(e) => setRpWa(e.target.value)}
                required
                hint="Strictement le même format / numéro qu’à l’inscription."
              />
              <PasswordField
                label="Nouveau mot de passe (min. 8)"
                autoComplete="new-password"
                value={rpNew}
                onChange={(e) => setRpNew(e.target.value)}
                required
                minLength={8}
              />
              <PasswordField
                label="Confirmer le mot de passe"
                autoComplete="new-password"
                value={rpNew2}
                onChange={(e) => setRpNew2(e.target.value)}
                required
                minLength={8}
              />
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-2xl bg-gradient-to-r from-brand-600 via-brand-500 to-violet-600 py-3.5 text-sm font-bold text-white shadow-glow transition disabled:opacity-50"
              >
                Valider — nouveau mot de passe
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
