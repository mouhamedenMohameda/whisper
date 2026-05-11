import { getAuthToken } from "./authStorage.js";

/**
 * Backend base URL. Empty string uses same-origin `/api/*` (Vite dev proxy or reverse proxy).
 * On Vercel, set `VITE_API_URL` to your Railway/Render backend origin.
 */
export function apiUrl(path) {
  const base = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
}

/**
 * Transforme le champ `detail` FastAPI/Pydantic (string, tableau 422, objet) en phrase lisible pour l’utilisateur.
 * @param {unknown} detail
 * @returns {string}
 */
export function normalizeApiDetail(detail) {
  if (detail == null || detail === "") return "";
  if (typeof detail === "string") {
    const s = detail.trim();
    return s;
  }
  if (Array.isArray(detail)) {
    const parts = [];
    for (const item of detail) {
      if (item == null) continue;
      if (typeof item === "string") {
        const t = item.trim();
        if (t) parts.push(t);
        continue;
      }
      if (typeof item === "object" && item.msg != null) {
        let m = String(item.msg).trim();
        // Pydantic v2 : préfixe systématique « Value error, » à retirer pour l’UI
        const low = m.toLowerCase();
        if (low.startsWith("value error,")) {
          m = m.slice(12).trim();
        }
        if (m) parts.push(m);
      }
    }
    if (parts.length === 0) return "";
    if (parts.length === 1) return parts[0];
    return parts.map((p, i) => `${i + 1}. ${p}`).join(" ");
  }
  if (typeof detail === "object") {
    const o = detail;
    if (Array.isArray(o.detail)) return normalizeApiDetail(o.detail);
    if (typeof o.msg === "string") return o.msg.trim();
  }
  return "";
}

function fallbackMessageForStatus(status) {
  switch (status) {
    case 400:
      return "Requête invalide — vérifie les informations saisies.";
    case 401:
      return "Tu n’es pas authentifié ou la session a expiré.";
    case 403:
      return "Accès refusé pour cette action.";
    case 404:
      return "Ressource introuvable.";
    case 422:
      return "Merci de corriger les champs indiqués avant de continuer.";
    case 429:
      return "Trop de requêtes — patiente un peu avant de réessayer.";
    case 500:
    case 502:
    case 503:
      return "Le serveur a rencontré un problème. Réessaie dans un instant.";
    default:
      return status ? `Erreur (${status}).` : "La requête a échoué.";
  }
}

/** Message utilisateur à partir d’un corps déjà parsé (évite une double lecture de `res.json()`). */
export function userFacingErrorFromParsedBody(data, status) {
  const fromDetail = normalizeApiDetail(data?.detail);
  if (fromDetail) return fromDetail;
  if (typeof data?.message === "string" && data.message.trim()) {
    return data.message.trim();
  }
  return fallbackMessageForStatus(status);
}

/**
 * Lit le corps JSON de la réponse erreur et renvoie un message utilisateur (jamais du JSON brut technique).
 * @param {Response} res
 */
export async function safeJsonDetail(res) {
  try {
    const data = await res.json();
    return userFacingErrorFromParsedBody(data, res.status);
  } catch {
    if (res.status === 0 || res.type === "opaque") {
      return "Connexion impossible — vérifie ta connexion ou que l’API tourne.";
    }
    return fallbackMessageForStatus(res.status) || res.statusText || "La requête a échoué.";
  }
}

/**
 * Parse `res.json()` une seule fois : renvoie `{ ok, data, errorMessage }`.
 * Utile pour les formulaires (succès + corps JSON sans double lecture).
 * @param {Response} res
 */
export async function parseJsonResponse(res) {
  /** @type {Record<string, unknown>} */
  let data = {};
  try {
    const text = await res.text();
    if (text.trim()) data = JSON.parse(text);
  } catch {
    data = {};
  }
  const ok = res.ok;
  const errorMessage = ok ? "" : userFacingErrorFromParsedBody(data, res.status);
  return { ok, data, errorMessage };
}

/** Headers for authenticated API calls. Pass true to include `Content-Type: application/json`. */
export function getAuthHeaders(json = false) {
  /** @type {Record<string, string>} */
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  const t = getAuthToken();
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

/** Fraction « globale » = envoi réseau pondéré + avancement traitement serveur (NDJSON). */
export const TRANSCRIBE_UPLOAD_WEIGHT = 0.34;
export const TRANSCRIBE_SERVER_WEIGHT = 0.66;

/** Code stable pour retrouver cette erreur sans dépendre d’instanceof (bundles / duplication). */
export const UPLOAD_INTERRUPTED_CODE = "LECTURAI_UPLOAD_INTERRUPTED";

/** Annulation navigateur ou coupure liaison avant la fin du flux — pas un message technique « erreur réseau ». */
export class UploadInterruptedError extends Error {
  constructor(
    message = "Ton envoi s’est arrêté : la transcription et l’upload doivent se terminer tant que cet onglet reste ouvert. Réessaie quand tu peux.",
  ) {
    super(message);
    this.name = "UploadInterruptedError";
    /** @type {typeof UPLOAD_INTERRUPTED_CODE} */
    this.code = UPLOAD_INTERRUPTED_CODE;
  }
}

/** @param {unknown} err */
export function isUploadInterruptedError(err) {
  return (
    typeof err === "object" &&
    err != null &&
    (err instanceof UploadInterruptedError ||
      /** @type {{ name?: unknown; code?: unknown }} */ (err).code === UPLOAD_INTERRUPTED_CODE ||
      /** @type {{ name?: unknown }} */ (err).name === "UploadInterruptedError")
  );
}

/** Statut terminal `failed` pour un job `/api/transcribe-jobs` — permet au client de distinguer erreur définitive. */
export class TranscriptionJobFailedError extends Error {
  /** @param {string} jobId */
  constructor(jobId, message = "") {
    super(message || "La transcription serveur a échoué.");
    this.name = "TranscriptionJobFailedError";
    /** @type {string} */
    this.jobId = jobId;
  }
}

/** @typedef {{
 *   transcriptionEngine?: "openai" | "local" | string;
 *   uiLocale?: string;
 *   onUploadProgress?: (uploadFrac0to1: number) => void;
 *   onUploadComplete?: () => void;
 *   onStreamEvent?: (ev: Record<string, unknown>) => void;
 * }} TranscribeProgressHandlers */

/**
 * Transcription avec `/api/transcribe-stream` (NDJSON pendant le traitement).
 * @param {string} speechLanguage Codes acceptés par le backend : fr | ar
 * @param {TranscribeProgressHandlers | ((combinedFrac: number)=>void)} handlers — ou callback légué `(0–1)` combinant envoi + serveur
 */
export function transcribeWithXHR(file, subject, speechLanguage, handlers) {
  /** @type {string} */
  const lang = speechLanguage === "ar" ? "ar" : "fr";

  /** @type {string} */
  const engineRaw =
    handlers && typeof handlers === "object" && typeof handlers.transcriptionEngine === "string"
      ? handlers.transcriptionEngine
      : "openai";
  const transcription_engine = engineRaw.trim().toLowerCase() === "local" ? "local" : "openai";

  /** @type {TranscribeProgressHandlers} */
  let h = {};
  const legacyCombined = typeof handlers === "function" ? /** @type {(n: number) => void} */ (handlers) : null;
  if (handlers && typeof handlers === "object") {
    h = handlers;
  }

  const ui_locale =
    typeof h.uiLocale === "string" && h.uiLocale.trim().toLowerCase().replace(/-/g, "_").startsWith("ar") ? "ar" : "fr";

  let uploadFrac = 0;
  let uploadComplete = false;
  let serverFrac = 0;

  const merged = () =>
    TRANSCRIBE_UPLOAD_WEIGHT * (uploadComplete ? 1 : uploadFrac) + TRANSCRIBE_SERVER_WEIGHT * serverFrac;

  const emitLegacyIfAny = () => {
    if (legacyCombined) legacyCombined(merged());
  };

  return new Promise((resolve, reject) => {
    let unloadLikeNavigation = false;
    /** À enregistrer le plus tôt possible : avant pagehide dans certains navigateurs ; avant onerrorXHR. */
    const markUnloadLike = () => {
      unloadLikeNavigation = true;
    };
    window.addEventListener("beforeunload", markUnloadLike);
    window.addEventListener("pagehide", markUnloadLike, true);
    window.addEventListener("unload", markUnloadLike);

    let settled = false;
    /** @returns {boolean} false si déjà résolu/refusé (évite double reject après abort + error). */
    const finalizeHandshake = () => {
      if (settled) return false;
      settled = true;
      window.removeEventListener("beforeunload", markUnloadLike);
      window.removeEventListener("pagehide", markUnloadLike, true);
      window.removeEventListener("unload", markUnloadLike);
      return true;
    };

    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl("/api/transcribe-stream"));
    const bearer = getAuthToken();
    if (bearer) xhr.setRequestHeader("Authorization", `Bearer ${bearer}`);

    let ndCursor = 0;
    /** @type {Record<string, unknown>|null} */
    let donePayload = null;
    /** @type {string|null} */
    let fatalFromStream = null;

    function drainNdjson() {
      const t = xhr.responseText;
      while (ndCursor < t.length) {
        const nl = t.indexOf("\n", ndCursor);
        if (nl === -1) break;
        const line = t.slice(ndCursor, nl).trim();
        ndCursor = nl + 1;
        if (!line) continue;
        /** @type {Record<string, unknown>} */
        let ev;
        try {
          ev = JSON.parse(line);
        } catch {
          continue;
        }
        const typ = typeof ev.type === "string" ? ev.type : "";
        if (typ === "error") {
          const d = normalizeApiDetail(ev.detail) || (typeof ev.message === "string" ? ev.message : "");
          fatalFromStream = d || "Transcription impossible.";
          if (typeof h.onStreamEvent === "function") h.onStreamEvent(ev);
          return;
        }
        if (typ === "status" || typ === "preview") {
          if (typeof ev.server_frac === "number") {
            serverFrac = Math.min(1, Math.max(0, ev.server_frac));
            emitLegacyIfAny();
          }
          if (typeof h.onStreamEvent === "function") h.onStreamEvent(ev);
        }
        if (typ === "done" && ev.result && typeof ev.result === "object") {
          serverFrac = 1;
          donePayload = /** @type {Record<string, unknown>} */ (ev.result);
          if (typeof h.onStreamEvent === "function") h.onStreamEvent(ev);
          emitLegacyIfAny();
          return;
        }
      }
    }

    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      uploadFrac = Math.min(1, Math.max(0, e.loaded / e.total));
      emitLegacyIfAny();
      if (typeof h.onUploadProgress === "function") h.onUploadProgress(uploadFrac);
    };

    xhr.upload.onload = () => {
      uploadComplete = true;
      uploadFrac = 1;
      emitLegacyIfAny();
      if (typeof h.onUploadComplete === "function") h.onUploadComplete();
    };

    xhr.onprogress = () => drainNdjson();
    xhr.onreadystatechange = () => {
      if (xhr.readyState >= 3) drainNdjson();
    };

    xhr.onload = () => {
      if (!finalizeHandshake()) return;
      drainNdjson();
      if (fatalFromStream) {
        reject(new Error(fatalFromStream));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        if (donePayload) {
          resolve(donePayload);
        } else {
          reject(new Error("Réponse transcription incomplète (pas d’événement terminé)."));
        }
      } else if (xhr.status === 0) {
        reject(
          unloadLikeNavigation
            ? new UploadInterruptedError()
            : new Error("L’envoi n’a pas abouti avant la réponse du serveur — vérifie ta connexion puis réessaie."),
        );
      } else {
        try {
          const data = JSON.parse(xhr.responseText);
          const msg =
            normalizeApiDetail(data?.detail) ||
            (typeof data?.message === "string" ? data.message.trim() : "") ||
            fallbackMessageForStatus(xhr.status);
          reject(new Error(msg || xhr.statusText || "Transcription impossible."));
        } catch {
          let msg = fallbackMessageForStatus(xhr.status) || xhr.statusText || "Transcription impossible.";
          if (xhr.status === 502 || xhr.status === 504) {
            msg +=
              " — Souvent le reverse-proxy (Nginx) : augmente proxy_read_timeout et proxy_send_timeout sur /api/transcribe-stream (ex. 3600s), proxy_buffering off, et client_body_timeout si l’upload est lent.";
          } else if (xhr.status >= 500) {
            msg += ` Code HTTP ${xhr.status}. Vérifie les journaux du serveur LecturAI.`;
          } else if (xhr.status) {
            msg += ` (HTTP ${xhr.status})`;
          }
          reject(new Error(msg));
        }
      }
    };

    xhr.onabort = () => {
      if (!finalizeHandshake()) return;
      reject(new UploadInterruptedError());
    };

    xhr.onerror = () => {
      if (!finalizeHandshake()) return;
      /*
       * Souvent xhr.status === 0 sur vraie coupure OU sur abandon navigateur ;
       * onerror peut précéder pagehide → on s’appuie sur beforeunload/pagehide(en capture)/unload +
       * on évite tout libellé du type « network error ».
       */
      if (unloadLikeNavigation) {
        reject(new UploadInterruptedError());
        return;
      }
      reject(
        new UploadInterruptedError(
          "Ton envoi s’est coupé avant la fin (connexion réseau, onglet ou page quittée pendant l’envoi). Garde cet onglet ouvert et réessaie.",
        ),
      );
    };

    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject || "General");
    fd.append("speech_language", lang);
    fd.append("transcription_engine", transcription_engine);
    fd.append("ui_locale", ui_locale);
    xhr.send(fd);
  });
}

/**
 * POST `/api/transcribe-jobs` — import du fichier puis file d’attente serveur (la transcription continue si l’utilisateur quitte la page après l’upload).
 * @param {TranscribeProgressHandlers} handlers
 * @returns {Promise<{ job_id: string }>}
 */
export function enqueueTranscribeJobWithXHR(file, subject, speechLanguage, handlers = {}) {
  /** @type {string} */
  const lang = speechLanguage === "ar" ? "ar" : "fr";
  /** @type {string} */
  const engineRaw = typeof handlers.transcriptionEngine === "string" ? handlers.transcriptionEngine : "openai";
  const transcription_engine = engineRaw.trim().toLowerCase() === "local" ? "local" : "openai";
  /** @type {TranscribeProgressHandlers} */
  let h = {};
  const legacyCombined = typeof handlers === "function" ? /** @type {(n: number) => void} */ (handlers) : null;
  if (handlers && typeof handlers === "object") {
    h = handlers;
  }

  const ui_locale =
    typeof h.uiLocale === "string" && h.uiLocale.trim().toLowerCase().replace(/-/g, "_").startsWith("ar") ? "ar" : "fr";

  let uploadFrac = 0;
  let uploadComplete = false;
  const serverFrac = 0;

  const merged = () =>
    TRANSCRIBE_UPLOAD_WEIGHT * (uploadComplete ? 1 : uploadFrac) + TRANSCRIBE_SERVER_WEIGHT * serverFrac;

  const emitLegacyIfAny = () => {
    if (legacyCombined) legacyCombined(merged());
  };

  return new Promise((resolve, reject) => {
    let unloadLikeNavigation = false;
    const markUnloadLike = () => {
      unloadLikeNavigation = true;
    };
    window.addEventListener("beforeunload", markUnloadLike);
    window.addEventListener("pagehide", markUnloadLike, true);
    window.addEventListener("unload", markUnloadLike);

    let settled = false;
    const finalizeHandshake = () => {
      if (settled) return false;
      settled = true;
      window.removeEventListener("beforeunload", markUnloadLike);
      window.removeEventListener("pagehide", markUnloadLike, true);
      window.removeEventListener("unload", markUnloadLike);
      return true;
    };

    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl("/api/transcribe-jobs"));
    const bearer = getAuthToken();
    if (bearer) xhr.setRequestHeader("Authorization", `Bearer ${bearer}`);

    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      uploadFrac = Math.min(1, Math.max(0, e.loaded / e.total));
      emitLegacyIfAny();
      if (typeof h.onUploadProgress === "function") h.onUploadProgress(uploadFrac);
    };

    xhr.upload.onload = () => {
      uploadComplete = true;
      uploadFrac = 1;
      emitLegacyIfAny();
      if (typeof h.onUploadComplete === "function") h.onUploadComplete();
    };

    xhr.onload = () => {
      if (!finalizeHandshake()) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        let data = {};
        try {
          if (xhr.responseText.trim()) data = JSON.parse(xhr.responseText);
        } catch {
          reject(new Error("Réponse création job inattendue."));
          return;
        }
        const jid = typeof data.job_id === "string" ? data.job_id : "";
        if (!jid) {
          reject(new Error("Identifiant de tâche manquant après import."));
          return;
        }
        resolve({ job_id: jid });
        return;
      }
      if (xhr.status === 0) {
        reject(
          unloadLikeNavigation ? new UploadInterruptedError() : new Error("Impossible de contacter le serveur."),
        );
        return;
      }
      let msg = fallbackMessageForStatus(xhr.status);
      try {
        if (xhr.responseText.trim()) {
          const parsed = JSON.parse(xhr.responseText);
          msg = normalizeApiDetail(parsed?.detail) || msg;
        }
      } catch {
        /* keep msg */
      }
      reject(new Error(msg));
    };

    xhr.onabort = () => {
      if (!finalizeHandshake()) return;
      reject(new UploadInterruptedError());
    };

    xhr.onerror = () => {
      if (!finalizeHandshake()) return;
      if (unloadLikeNavigation) {
        reject(new UploadInterruptedError());
        return;
      }
      reject(
        new UploadInterruptedError(
          "Ton envoi s’est coupé avant la fin (connexion ou page quittée pendant l’import). Réessaie.",
        ),
      );
    };

    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject || "General");
    fd.append("speech_language", lang);
    fd.append("transcription_engine", transcription_engine);
    fd.append("ui_locale", ui_locale);
    xhr.send(fd);
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Poll jusqu’à statut terminal `done` | `failed`. `include_result` est forcé tant que pas terminé afin de limiter les allers-retours après succès uniquement avec `done`.
 * @param {string} jobId
 * @param {{ onTick?: (row: Record<string, unknown>) => void; intervalMs?: number; signal?: AbortSignal }} opts
 */
export async function waitForTerminalTranscriptionJob(jobId, opts = {}) {
  const { onTick, intervalMs = 2400, signal } = opts;
  /** @type {Record<string, unknown> | null} */
  let lastRow = null;

  while (true) {
    if (signal?.aborted) throw new Error("Interrompu.");
    let res = await fetch(
      `${apiUrl("/api/transcribe-jobs/")}${encodeURIComponent(jobId)}?include_result=false`,
      {
        headers: getAuthHeaders(false),
        signal,
      },
    );
    let { ok, data, errorMessage } = await parseJsonResponse(res);
    if (!ok || !data) throw new Error(errorMessage || "Lecture du statut de transcription impossible.");
    /** @type {Record<string, unknown>} */
    let row = typeof data === "object" ? /** @type {Record<string, unknown>} */ (data) : {};
    lastRow = row;
    if (typeof onTick === "function") onTick(row);

    const st = typeof row.status === "string" ? row.status : "";
    if (st === "failed") {
      const d = typeof row.error_detail === "string" ? row.error_detail.trim() : "";
      throw new TranscriptionJobFailedError(jobId, d || "La transcription serveur a échoué.");
    }
    if (st === "done") {
      res = await fetch(
        `${apiUrl("/api/transcribe-jobs/")}${encodeURIComponent(jobId)}?include_result=true`,
        { headers: getAuthHeaders(false), signal },
      );
      ({ ok, data, errorMessage } = await parseJsonResponse(res));
      if (!ok || !data) throw new Error(errorMessage || "Impossible de récupérer le résultat de transcription.");
      row = typeof data === "object" ? /** @type {Record<string, unknown>} */ (data) : {};
      if (typeof onTick === "function") onTick(row);
      return row;
    }

    await sleep(intervalMs);
  }
}

export async function listTranscriptionJobs() {
  const res = await fetch(apiUrl("/api/transcribe-jobs"), {
    headers: getAuthHeaders(false),
  });
  const { ok, data, errorMessage } = await parseJsonResponse(res);
  if (!ok) throw new Error(errorMessage || "Liste des fichiers inaccessible.");
  return Array.isArray(data?.items) ? data.items : [];
}

/**
 * @param {string} jobId
 * @param {{ include_result?: boolean; signal?: AbortSignal }} [opts]
 * @returns {Promise<Record<string, unknown>>}
 */
export async function getTranscriptionJob(jobId, opts = {}) {
  const { include_result = false, signal } = opts;
  const qs = include_result ? "include_result=true" : "include_result=false";
  const res = await fetch(`${apiUrl("/api/transcribe-jobs/")}${encodeURIComponent(jobId)}?${qs}`, {
    headers: getAuthHeaders(false),
    signal,
  });
  const { ok, data, errorMessage } = await parseJsonResponse(res);
  if (!ok || !data || typeof data !== "object") {
    throw new Error(errorMessage || "Impossible de lire cette tâche de transcription.");
  }
  return /** @type {Record<string, unknown>} */ (data);
}

/** Annule un job encore en file (statut `queued` uniquement). */
export async function cancelTranscriptionJob(jobId) {
  const res = await fetch(`${apiUrl("/api/transcribe-jobs/")}${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    headers: getAuthHeaders(true),
  });
  const { ok, data, errorMessage } = await parseJsonResponse(res);
  if (!ok) throw new Error(errorMessage || "Annulation impossible.");
  return data;
}

/**
 * @param {string} transcript
 * @param {string} subject
 * @param {Record<string, unknown> | null | undefined} [transcriptMixedView] repli si pas d’ASR annoté
 * @param {unknown[] | null | undefined} [asrPassagesAnnotated] passages ASR annotés (prioritaire pour la génération)
 */
/**
 * Synthèse + sujet d’aide à la lecture (optionnel, après transcription).
 * @param {string} transcript
 * @param {string} subject
 * @param {"fr"|"ar"|string} speechLanguage
 */
export async function requestTranscriptInsight(transcript, subject, speechLanguage) {
  const res = await fetch(apiUrl("/api/transcript-insight"), {
    method: "POST",
    headers: getAuthHeaders(true),
    body: JSON.stringify({
      transcript,
      subject: subject || "General",
      speech_language: speechLanguage === "ar" ? "ar" : "fr",
    }),
  });
  const { ok, data, errorMessage } = await parseJsonResponse(res);
  if (!ok || !data || typeof data !== "object") {
    throw new Error(errorMessage || "Analyse indisponible.");
  }
  return /** @type {Record<string, unknown>} */ (data);
}

export async function generateLesson(transcript, subject, transcriptMixedView, asrPassagesAnnotated, uiLanguage) {
  const body = {
    transcript,
    subject: subject || "General",
  };
  if (typeof uiLanguage === "string" && uiLanguage.trim()) {
    body.language = uiLanguage.trim().toLowerCase().startsWith("ar") ? "ar" : "fr";
  }
  if (Array.isArray(asrPassagesAnnotated) && asrPassagesAnnotated.length > 0) {
    body.asr_passages_annotated = asrPassagesAnnotated;
  }
  if (transcriptMixedView && typeof transcriptMixedView === "object") {
    body.transcript_mixed_view = transcriptMixedView;
  }
  const res = await fetch(apiUrl("/api/generate"), {
    method: "POST",
    headers: getAuthHeaders(true),
    body: JSON.stringify(body),
  });
  const { ok, data, errorMessage } = await parseJsonResponse(res);
  if (!ok) throw new Error(errorMessage || "Génération impossible.");
  return data;
}

/**
 * @param {string[]} jobPublicIds identifiants publics (hex 32) des tâches `/transcribe-jobs`
 * @param {number} stars 1–5
 */
export async function submitTranscriptionJobRatings(jobPublicIds, stars) {
  const ids = [...new Set((jobPublicIds || []).map((x) => String(x || "").trim()).filter(Boolean))];
  if (!ids.length) throw new Error("Aucune tâche à noter.");
  const res = await fetch(apiUrl("/api/feedback/transcription-rating"), {
    method: "POST",
    headers: getAuthHeaders(true),
    body: JSON.stringify({ job_public_ids: ids, stars: Number(stars) }),
  });
  const { ok, errorMessage } = await parseJsonResponse(res);
  if (!ok) throw new Error(errorMessage || "Enregistrement de la note impossible.");
}

/**
 * @param {string} message
 * @param {string} [uiLocale] fr | ar
 */
export async function submitAppFeedback(message, uiLocale) {
  const loc =
    typeof uiLocale === "string" && uiLocale.trim().toLowerCase().replace(/-/g, "_").startsWith("ar") ? "ar" : "fr";
  const res = await fetch(apiUrl("/api/feedback/suggestion"), {
    method: "POST",
    headers: getAuthHeaders(true),
    body: JSON.stringify({ message: String(message || "").trim(), ui_locale: loc }),
  });
  const { ok, errorMessage } = await parseJsonResponse(res);
  if (!ok) throw new Error(errorMessage || "Envoi impossible.");
}

export async function downloadExport(kind, body, downloadName) {
  const res = await fetch(apiUrl(kind === "pdf" ? "/api/export/pdf" : "/api/export/docx"), {
    method: "POST",
    headers: getAuthHeaders(true),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const msg = await safeJsonDetail(res);
    throw new Error(msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName;
  a.click();
  URL.revokeObjectURL(url);
}
