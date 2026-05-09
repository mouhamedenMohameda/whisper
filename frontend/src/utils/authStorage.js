const TOKEN_KEY = "lecturai_access_token";
const PROFILE_KEY = "lecturai_profile";

/** @typedef {{ email: string, nni_masked?: string, whatsapp_masked?: string, is_admin?: boolean }} AuthProfile */

export function getAuthToken() {
  return typeof localStorage !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
}

/** @param {AuthProfile | null | undefined} user */
export function setAuthSession(accessToken, user) {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(TOKEN_KEY, accessToken);
  if (user && typeof user === "object") {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(user));
  }
}

/** @returns {AuthProfile | null} */
export function getAuthProfile() {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(PROFILE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function clearAuthSession() {
  if (typeof localStorage === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(PROFILE_KEY);
}
