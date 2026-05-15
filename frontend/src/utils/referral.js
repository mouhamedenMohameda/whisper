/**
 * Capture & persistance du code de parrainage entrant via `?ref=CODE` dans l'URL.
 *
 * Le code est stocké en `localStorage` à la racine de l'app (lecture au mount de `<App />`),
 * puis envoyé une seule fois au backend dans `POST /api/auth/register`. Il est purgé après
 * une inscription réussie pour éviter de polluer une future création de compte.
 *
 * Alphabet attendu : alphanum sans 0/O/I/1 (cf. `referrals._CODE_ALPHABET` côté backend),
 * longueur 6–16. Tout code non conforme est silencieusement ignoré côté front et côté back.
 */

const STORAGE_KEY = "lecturai_ref_code";
const REF_RE = /^[A-HJ-NP-Z2-9]{6,16}$/;

/** Normalise vers la même règle que le backend (`referrals.normalize_code`). */
export function normalizeReferralCode(raw) {
  if (typeof raw !== "string") return null;
  const s = raw.trim().toUpperCase();
  if (!s) return null;
  return REF_RE.test(s) ? s : null;
}

/**
 * À appeler au mount de l'app : si `?ref=XXX` est dans l'URL on le persiste.
 * Nettoie aussi l'URL pour éviter qu'un partage maladroit ne propage des codes croisés.
 */
export function captureReferralFromUrl() {
  try {
    const url = new URL(window.location.href);
    const raw = url.searchParams.get("ref");
    const norm = normalizeReferralCode(raw);
    if (norm) {
      localStorage.setItem(STORAGE_KEY, norm);
      url.searchParams.delete("ref");
      const clean = url.pathname + (url.searchParams.toString() ? `?${url.searchParams.toString()}` : "") + url.hash;
      window.history.replaceState({}, "", clean);
    }
  } catch {
    /* localStorage indisponible (Safari privé, etc.) → ignore silencieusement */
  }
}

export function getPendingReferralCode() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return normalizeReferralCode(v);
  } catch {
    return null;
  }
}

export function clearPendingReferralCode() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* noop */
  }
}

/** Construit l'URL de partage à partir du code de parrainage de l'utilisateur courant. */
export function buildReferralShareUrl(code) {
  const norm = normalizeReferralCode(code);
  if (!norm) return null;
  try {
    const base = window.location.origin;
    return `${base}/?ref=${norm}`;
  } catch {
    return null;
  }
}
