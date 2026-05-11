/** Titres ## qui introduisent une section fiches / flashcards (FR/EN/AR). */
function isFlashcardsSectionTitle(t) {
  return /(flashcards|fiches|cartes?\s*m[ée]moire|بطاقات|فلاش\s*كارد|بطاقة\s*مراجعة)/i.test(
    String(t || ""),
  );
}

export function slugify(raw) {
  const s = String(raw || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/(^-|-$)/g, "");
  return s || "section";
}

export function extractNavHeadings(markdown) {
  const items = [];
  const re = /^##\s+(.+)$/gm;
  let m;
  while ((m = re.exec(markdown || "")) !== null) {
    const title = m[1].trim();
    items.push({ title, id: slugify(title) });
  }
  return items;
}

export function parseQuiz(md) {
  const text = String(md || "");
  if (!text.trim()) return [];

  // 1) Extraire une section quiz si elle existe, sinon parser tout le texte.
  //    Tolère titres en FR/EN + numérotation variable.
  const h2 = [...text.matchAll(/^##\s+(.+)$/gm)].map((m) => ({
    title: (m[1] || "").trim(),
    idx: m.index ?? 0,
  }));

  const isQuizHeading = (t) =>
    /(practice\s*quiz|quiz|qcm|questions?\s*(?:à\s*choix|a\s*choix|choix)\s*multiples?|questions?\s*multi|exercices?\s*\(?qcm\)?)/i.test(
      String(t || ""),
    );
  const isQuizHeadingAr = (t) => /(اختبار|امتحان|أسئلة|اسئلة|اختبار\s*قصير)/i.test(String(t || ""));
  let section = text;
  const start = h2.findIndex((h) => isQuizHeading(h.title) || isQuizHeadingAr(h.title));
  if (start !== -1) {
    const end = h2.slice(start + 1).find((h) => isFlashcardsSectionTitle(h.title) || /^(\d+)\.\s*/.test(h.title));
    const s0 = h2[start].idx;
    const s1 = end ? end.idx : text.length;
    section = text.slice(s0, s1);
  } else {
    // Fallback "ancienne structure" : 6 -> 7.
    section = text.split(/##\s*6\./i)[1]?.split(/##\s*7\./i)[0] || text;
  }

  // 2) Parser en mode "ligne à ligne", tolérant aux variations.
  const lines = section.split(/\r?\n/);
  /** @type {{ question: string; options: string[]; correct: number; explanation: string }[]} */
  const out = [];

  let curQ = "";
  let curOpts = [];
  let curCorrect = -1;
  let curExpl = "";

  const flush = () => {
    const q = curQ.trim();
    if (!q) return;
    // Normalise les options "A) ..." etc
    const opts = curOpts.map((o) => o.trim()).filter(Boolean);
    if (opts.length >= 2) {
      out.push({ question: q, options: opts, correct: curCorrect, explanation: curExpl.trim() });
    }
    curQ = "";
    curOpts = [];
    curCorrect = -1;
    curExpl = "";
  };

  const isQuestionLine = (s) =>
    /^(?:\*\*)?\s*(?:Q(?:uestion)?\s*\d+|\d+)\s*[\.\):\-–]\s*\S/i.test(s) ||
    /^\s*\*\*Q(?:uestion)?\s*\d+/i.test(s);

  const parseQuestionText = (s) => {
    let x = String(s || "").trim();
    // retire markdown bold wrappers
    x = x.replace(/^\*\*+/, "").replace(/\*\*+$/, "").trim();
    // retire "Q1." / "Question 1:" / "1)" etc
    x = x.replace(/^(?:Q(?:uestion)?\s*)?\d+\s*[\.\):\-–]\s*/i, "").trim();
    x = x.replace(/^Q(?:uestion)?\s*\d+\s*/i, "").trim();
    return x;
  };

  const parseOption = (s) => {
    const m = String(s || "").match(/^\s*(?:[-*]\s*)?([A-D])\s*[\)\.\-:]\s*(.+?)\s*(✅)?\s*$/i);
    if (!m) return null;
    const label = String(m[1] || "").toUpperCase();
    const txt = String(m[2] || "").replace(/✅/g, "").trim();
    const ok = Boolean(m[3]);
    return { label, txt, ok };
  };

  const isExplanationLine = (s) => /^\s*(?:\*+\s*)?(?:Explanation|Explication|تفسير|الشرح)\s*:\s*/i.test(s);
  const parseExplanation = (s) =>
    String(s || "")
      .replace(/^\s*(?:\*+\s*)?(?:Explanation|Explication|تفسير|الشرح)\s*:\s*/i, "")
      .trim();

  for (const raw of lines) {
    const s = String(raw || "").trim();
    if (!s) continue;

    // Nouveau début de question.
    if (isQuestionLine(s) && !parseOption(s)) {
      flush();
      curQ = parseQuestionText(s);
      continue;
    }

    const opt = parseOption(s);
    if (opt) {
      const idx = curOpts.length;
      curOpts.push(`${opt.label}) ${opt.txt}`);
      if (opt.ok) curCorrect = idx;
      continue;
    }

    if (isExplanationLine(s)) {
      const part = parseExplanation(s);
      curExpl = curExpl ? `${curExpl} ${part}` : part;
      continue;
    }

    // Lignes continues : compléter question si pas d’options, sinon compléter explication.
    if (curQ && curOpts.length === 0) {
      curQ = `${curQ} ${s}`.trim();
    } else if (curQ && curOpts.length > 0) {
      curExpl = curExpl ? `${curExpl} ${s}`.trim() : s;
    }
  }

  flush();

  // 3) Fallback ultime : si rien n’a été détecté mais on voit des ✅, essayer de regrouper.
  if (out.length === 0 && /✅/.test(section)) {
    // Très simple : repère des blocs entre lignes vides et tente de parser.
    const blocks = section.split(/\n{2,}/);
    for (const b of blocks) {
      const ls = b.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
      if (ls.length < 4) continue;
      const opts = ls.map(parseOption).filter(Boolean);
      if (opts.length < 2) continue;
      const qLine = ls.find((x) => !parseOption(x) && !isExplanationLine(x));
      if (!qLine) continue;
      const options = opts.map((o) => `${o.label}) ${o.txt}`);
      const cix = opts.findIndex((o) => o.ok);
      const explLine = ls.find((x) => isExplanationLine(x));
      const explanation = explLine ? parseExplanation(explLine) : "";
      out.push({ question: parseQuestionText(qLine), options, correct: cix, explanation });
      if (out.length >= 12) break;
    }
  }

  return out;
}

/**
 * Extrait le bloc markdown des fiches : même logique flexible que le quiz (titres ##),
 * puis repli sur la structure numérotée demandée au modèle.
 */
function extractFlashcardsMarkdownSection(text) {
  const md = String(text || "");
  if (!md.trim()) return "";

  const h2 = [...md.matchAll(/^##\s+(.+)$/gm)].map((m) => ({
    title: (m[1] || "").trim(),
    idx: m.index ?? 0,
  }));

  const flashIdx = h2.findIndex((h) => isFlashcardsSectionTitle(h.title));
  if (flashIdx !== -1) {
    const s0 = h2[flashIdx].idx;
    const next = h2[flashIdx + 1];
    return next ? md.slice(s0, next.idx) : md.slice(s0);
  }

  let s = md.split(/##\s*7\.\s*FLASHCARDS/i)[1]?.split(/##\s*8\./i)[0] || "";
  if (s.trim()) return s;

  s = md.split(/##\s*7\./i)[1]?.split(/##\s*8\./i)[0] || "";
  if (s.trim()) return s;

  // Dernier recours : fiches après le quiz, avant la section 8 (titres parfois non standard).
  const tail = md.split(/##\s*6\.\s*PRACTICE QUIZ/i)[1]?.split(/##\s*8\./i)[0] || "";
  if (/\bQ\s*:\s*/i.test(tail) && /\bA\s*:\s*/i.test(tail) && /\/\s*A\s*:\s*|^\s*A\s*:/im.test(tail)) {
    return tail;
  }

  return "";
}

export function parseFlashcards(md) {
  let section = extractFlashcardsMarkdownSection(md);
  if (!section.trim() && /\bQ\s*:\s*.+\bA\s*:\s*.+/is.test(String(md || ""))) {
    section = String(md || "");
  }

  const seen = new Set();
  /** @type {{ q: string; a: string }[]} */
  const cards = [];
  const push = (q, a) => {
    const qq = String(q || "").trim();
    const aa = String(a || "").trim();
    if (!qq || !aa) return;
    const key = `${qq}\u241F${aa}`;
    if (seen.has(key)) return;
    seen.add(key);
    cards.push({ q: qq, a: aa });
  };

  let m;
  const sameLine = /\bQ\s*:\s*(.+?)\s*\/\s*A\s*:\s*(.+)/gi;
  while ((m = sameLine.exec(section)) !== null) {
    push(m[1], m[2]);
  }

  const multiline =
    /\*\*(?:Card|Carte|Fiche)\s*\d+\*\*[\s\r\n]*Q\s*:\s*([\s\S]+?)[\s\r\n]+A\s*:\s*([\s\S]+?)(?=\*\*(?:Card|Carte|Fiche)\s*\d+\*\*|\r?\n##|$)/gi;
  while ((m = multiline.exec(section)) !== null) {
    push(m[1], m[2]);
  }

  const loose = /\bQ\s*:\s*([\s\S]+?)\r?\n+\s*A\s*:\s*([\s\S]+?)(?=\r?\n\s*Q\s*:\s|\r?\n##|\r?\n\*\*(?:Card|Carte|Fiche)\s*\d+\*\*|$)/gi;
  if (cards.length < 12) {
    while ((m = loose.exec(section)) !== null) {
      push(m[1], m[2]);
    }
  }

  return cards;
}
