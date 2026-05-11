const STORAGE_KEY = "lecturai-rating-prompt-handled-v1";
const MAX_KEYS = 80;

/** @param {string[]} jobPublicIds */
export function ratingPromptSignature(jobPublicIds) {
  const ids = (jobPublicIds || []).map((x) => String(x || "").trim().toLowerCase()).filter(Boolean);
  ids.sort();
  return ids.join("|");
}

/** @param {string[]} jobPublicIds */
export function wasRatingPromptHandled(jobPublicIds) {
  const sig = ratingPromptSignature(jobPublicIds);
  if (!sig) return true;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(arr)) return false;
    return arr.includes(sig);
  } catch {
    return false;
  }
}

/** @param {string[]} jobPublicIds */
export function markRatingPromptHandled(jobPublicIds) {
  const sig = ratingPromptSignature(jobPublicIds);
  if (!sig) return;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    let arr = [];
    try {
      arr = raw ? JSON.parse(raw) : [];
    } catch {
      arr = [];
    }
    if (!Array.isArray(arr)) arr = [];
    if (arr.includes(sig)) return;
    const next = [...arr, sig].slice(-MAX_KEYS);
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
}
