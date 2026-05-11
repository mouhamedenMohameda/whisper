/**
 * Libellés d’erreur transcription affichés à l’utilisateur final — sans fuite de fournisseur / stack interne.
 * @param {string | undefined | null} errorDetail
 * @param {import("i18next").TFunction} t
 */
export function userFacingTranscriptionJobFailure(errorDetail, message, t) {
  const d = typeof errorDetail === "string" ? errorDetail.trim() : "";
  const m = typeof message === "string" ? message.trim() : "";
  const blob = d + m;
  const technical =
    /openai|whisper|ffmpeg|api_key|apikey|status_code|traceback|exception|uvicorn|internal|torch\.|pytorch|\bcuda\b|\bmps\b|autograd|in_features\s*=|out_features\s*=|\bbias\s*=\s*true\b|torch\.nn|nn\.parameter|linear\s*\(\s*in_features/i.test(
      blob,
    );
  if (technical) {
    return t("bgJobs.failedGeneric");
  }
  const low = d.toLowerCase();
  if (low.includes("trop volumineux") || /\b25\b.*mo|mo.*\b25\b/i.test(d) || low.includes("too large")) {
    return t("bgJobs.failedFileSize");
  }
  if (low.includes("authentification") || low.includes("authentication")) {
    return t("bgJobs.failedService");
  }
  if (low.includes("connexion") || low.includes("connection")) {
    return t("bgJobs.failedNetwork");
  }
  if (d.length > 0 && d.length < 220 && !/[{}[\]]/.test(d)) {
    return d;
  }
  if (m.length > 0 && m.length < 160 && !/[{}[\]]/.test(m) && !/whisper|openai/i.test(m)) {
    return m;
  }
  return t("bgJobs.failedGeneric");
}
