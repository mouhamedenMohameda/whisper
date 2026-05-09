export function estimateTokensFromText(text) {
  if (!text || !String(text).trim()) return 0;
  return Math.max(1, Math.round(String(text).length / 4));
}

export function formatMru(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  return v.toLocaleString("fr-MR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
}
