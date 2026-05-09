const STORAGE_KEY = "lecturai-transcription-history-v1";
const MAX_ENTRIES = 100;

/**
 * @typedef {object} HistoryUsage
 * @property {number} whisperAudioSeconds
 * @property {number} whisperBilledMru
 * @property {number} whisperApiEstimatedTokensSum
 * @property {number} [mixedLangPromptTokens]
 * @property {number} [mixedLangCompletionTokens]
 * @property {number} claudeInput
 * @property {number} claudeOutput
 * @property {number} claudeBilledMru
 */

/**
 * @typedef {object} TranscriptMixedViewPayload
 * @property {unknown[]} blocks
 * @property {string} [plain_text]
 * @property {number} [foreign_segment_count]
 * @property {number} [high_reliability_block_count]
 */

/**
 * @typedef {object} TranscriptionHistoryItem
 * @property {string} id
 * @property {string} createdAt ISO
 * @property {string} displayTitle
 * @property {string[]} filenames
 * @property {string} transcript
 * @property {TranscriptMixedViewPayload|null} [transcriptMixedView]
 * @property {string} subject
 * @property {"fr"|"ar"|undefined} speechLanguage Langue imposée au moment de la transcription
 * @property {string} language
 * @property {number} wordCount
 * @property {number} durationMinutes
 * @property {HistoryUsage} usage
 * @property {string|null} lesson
 */

export function loadHistory() {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function persist(entries) {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)));
}

/**
 * @param {TranscriptionHistoryItem} entry
 */
export function prependEntry(entry) {
  const prev = loadHistory().filter((e) => e.id !== entry.id);
  prev.unshift(entry);
  persist(prev);
}

/**
 * @param {string} id
 * @param {Partial<TranscriptionHistoryItem>} patch
 */
export function updateEntry(id, patch) {
  const prev = loadHistory();
  const next = prev.map((e) => {
    if (e.id !== id) return e;
    const merged = { ...e, ...patch };
    if (patch.usage != null && e.usage != null) {
      merged.usage = { ...e.usage, ...patch.usage };
    }
    return merged;
  });
  persist(next);
}

/** @param {string} id */
export function removeEntry(id) {
  persist(loadHistory().filter((e) => e.id !== id));
}

/** @param {string} id */
export function getEntry(id) {
  return loadHistory().find((e) => e.id === id) ?? null;
}
