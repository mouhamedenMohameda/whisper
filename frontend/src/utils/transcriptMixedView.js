/**
 * Fusionne plusieurs réponses /transcribe (fichiers multiples) en une seule vue structurée.
 *
 * @param {Array<{ label: string; view: Record<string, unknown> | null | undefined }>} parts
 */
export function mergeTranscriptMixedViews(parts) {
  if (!parts || parts.length === 0) return null;
  /** @type {unknown[]} */
  const blocks = [];
  let plain = "";
  let foreign = 0;
  let highRel = 0;
  const multi = parts.length > 1;
  let firstHeader = true;
  for (const { label, view } of parts) {
    if (!view || !Array.isArray(view.blocks)) continue;
    if (multi) {
      blocks.push({ kind: "file_header", text: `=== ${label} ===` });
      plain += `${firstHeader ? "" : "\n\n---\n\n"}=== ${label} ===\n\n`;
      firstHeader = false;
    }
    blocks.push(...view.blocks);
    plain += typeof view.plain_text === "string" ? view.plain_text : "";
    foreign += Number(view.foreign_segment_count ?? 0) || 0;
    highRel += Number(view.high_reliability_block_count ?? 0) || 0;
  }
  if (blocks.length === 0) return null;
  return {
    blocks,
    plain_text: plain,
    foreign_segment_count: foreign,
    high_reliability_block_count: highRel,
  };
}
