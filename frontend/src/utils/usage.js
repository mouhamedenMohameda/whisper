export function estimateTokensFromText(text) {
  if (!text || !String(text).trim()) return 0;
  return Math.max(1, Math.round(String(text).length / 4));
}

/**
 * Formate un montant MRU pour l’UI. Sous 1 MRU, plus de décimales pour éviter
 * d’afficher « ~0 » alors que la transcription ou le cours ont un coût réel mais minuscule.
 */

export function formatMru(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  if (v === 0) return "0";
  try {
    const abs = Math.abs(v);
    const maxFrac = abs < 1 ? 6 : 2;
    // On force au moins 2 décimales pour la cohérence visuelle dans les tableaux de prix
    return v.toLocaleString("fr-FR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: maxFrac,
    });
  } catch {
    return v.toFixed(2);
  }
}
