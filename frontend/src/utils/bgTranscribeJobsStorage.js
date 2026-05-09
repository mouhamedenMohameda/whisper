const STORAGE_KEY = "lecturai-bg-transcribe-job-ids";

/** @returns {string[]} */
export function loadBgTranscribeJobIds() {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? Array.from(new Set(arr.map((x) => String(x)))) : [];
  } catch {
    return [];
  }
}

/** @param {string} jobPublicId */
export function persistBgTranscribeJobId(jobPublicId) {
  if (typeof localStorage === "undefined" || !jobPublicId) return;
  const prev = loadBgTranscribeJobIds().filter((x) => x !== jobPublicId);
  prev.unshift(String(jobPublicId));
  localStorage.setItem(STORAGE_KEY, JSON.stringify(prev.slice(0, 48)));
}

/** @param {string} jobPublicId */
export function forgetBgTranscribeJobId(jobPublicId) {
  if (typeof localStorage === "undefined" || !jobPublicId) return;
  const next = loadBgTranscribeJobIds().filter((x) => x !== String(jobPublicId));
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}
