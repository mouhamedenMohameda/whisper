const STORAGE_KEY = "lecturai-transcription-history-v1";
const MAX_ENTRIES = 100;
const LESSON_KEY_PREFIX = "lecturai-lesson-v1:";

function lessonKey(id) {
  return `${LESSON_KEY_PREFIX}${String(id || "")}`;
}

/** Stocke le markdown du cours séparément (évite quota localStorage sur gros blobs). */
function persistLesson(id, lesson) {
  if (typeof localStorage === "undefined") return;
  const k = lessonKey(id);
  try {
    if (lesson && String(lesson).trim()) localStorage.setItem(k, String(lesson));
    else localStorage.removeItem(k);
  } catch {
    // QuotaExceededError ou stockage indisponible : on n'écrase pas l'historique.
  }
}

function loadLesson(id) {
  if (typeof localStorage === "undefined") return null;
  const k = lessonKey(id);
  try {
    const v = localStorage.getItem(k);
    return v && v.trim() ? v : null;
  } catch {
    return null;
  }
}

function removeLesson(id) {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.removeItem(lessonKey(id));
  } catch {
    /* noop */
  }
}

/**
 * @typedef {object} HistoryUsage
 * @property {number} whisperAudioSeconds
 * @property {number} whisperBilledMru
 * @property {number} whisperApiEstimatedTokensSum
 * @property {number} [mixedLangPromptTokens]
 * @property {number} [mixedLangCompletionTokens]
 * @property {number} groqLessonInput
 * @property {number} groqLessonOutput
 * @property {number} groqLessonBilledMru
 * @property {number} [claudeInput] héritage (anciennes sessions)
 * @property {number} [claudeOutput]
 * @property {number} [claudeBilledMru]
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
 * @property {unknown[]} [asrPassagesAnnotated] passages ASR + fiabilité (réponse transcription)
 * @property {string} subject
 * @property {"fr"|"ar"|undefined} speechLanguage Langue imposée au moment de la transcription
 * @property {string} language
 * @property {number} wordCount
 * @property {number} durationMinutes
 * @property {HistoryUsage} usage
 * @property {string|null} lesson
 * @property {string[]} [transcriptionJobPublicIds] identifiants publics des tâches serveur (pour note post-transcription)
 */

export function loadHistory() {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    const items = Array.isArray(arr) ? arr : [];
    // Hydrate les cours depuis le stockage dédié.
    return items.map((e) => {
      if (!e || typeof e !== "object") return e;
      if (e.lesson && String(e.lesson).trim()) return e;
      const l = loadLesson(e.id);
      return l ? { ...e, lesson: l } : e;
    });
  } catch {
    return [];
  }
}

function persist(entries) {
  if (typeof localStorage === "undefined") return;
  // Historique compact : on ne met pas les gros markdown dans la liste.
  const trimmed = entries.slice(0, MAX_ENTRIES).map((e) => {
    if (!e || typeof e !== "object") return e;
    if (e.lesson && String(e.lesson).trim()) {
      persistLesson(e.id, e.lesson);
      return { ...e, lesson: null };
    }
    return e;
  });
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // QuotaExceededError: on tente une version encore plus compacte sans transcriptMixedView.
    try {
      const skinny = trimmed.map((e) => {
        if (!e || typeof e !== "object") return e;
        const { transcriptMixedView, asrPassagesAnnotated, ...rest } = e;
        return rest;
      });
      localStorage.setItem(STORAGE_KEY, JSON.stringify(skinny));
    } catch {
      /* abandon silencieux */
    }
  }
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
  if (patch && Object.prototype.hasOwnProperty.call(patch, "lesson")) {
    // Garantit la persistance même si l'historique dépasse le quota.
    persistLesson(id, patch.lesson);
  }
  persist(next);
}

/** @param {string} id */
export function removeEntry(id) {
  removeLesson(id);
  persist(loadHistory().filter((e) => e.id !== id));
}

/** @param {string} id */
export function getEntry(id) {
  return loadHistory().find((e) => e.id === id) ?? null;
}
