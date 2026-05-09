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
  const section =
    md.split(/##\s*6\.\s*PRACTICE QUIZ/i)[1]?.split(/##\s*7\./i)[0] || md || "";
  const questions = [];
  const re =
    /\*\*Q(\d+)\.\s*([^*]+)\*\*([\s\S]*?)(?=\*\*Q\d+\.|##\s*7\.|$)/gi;
  let m;
  while ((m = re.exec(section)) !== null) {
    const question = m[2].trim();
    const body = m[3];
    const options = [];
    let correct = -1;
    let idx = 0;

    const optIt = body.matchAll(
      /^\s*[-*]\s*([A-D])\)\s*(.+?)(✅)?\s*$/gim,
    );
    for (const om of optIt) {
      const label = om[1];
      const txt = om[2].replace(/✅/g, "").trim();
      options.push(`${label}) ${txt}`);
      if (om[3]) correct = idx;
      idx += 1;
    }

    const expl =
      body.match(/\*?\s*Explanation:\s*(.+?)(?:\*|\n\n|$)/ims) ||
      body.match(/\*Explanation:\s*(.+?)\*/im);
    const explanation = expl ? expl[1].replace(/\s+/g, " ").trim() : "";

    if (options.length >= 2) {
      questions.push({ question, options, correct, explanation });
    }
  }
  return questions;
}

export function parseFlashcards(md) {
  const section =
    md.split(/##\s*7\.\s*FLASHCARDS/i)[1]?.split(/##\s*8\./i)[0] || "";
  const cards = [];

  const sameLine = /Q:\s*(.+?)\s*\/\s*A:\s*(.+)/gi;
  let m;
  while ((m = sameLine.exec(section)) !== null) {
    cards.push({ q: m[1].trim(), a: m[2].trim() });
  }

  const multiline =
    /\*\*Card\s*\d+\*\*[\s\r\n]*Q:\s*([\s\S]+?)[\s\r\n]+A:\s*([\s\S]+?)(?=\*\*Card|\n##|$)/gi;
  while ((m = multiline.exec(section)) !== null) {
    cards.push({ q: m[1].trim(), a: m[2].trim() });
  }

  const loose = /Q:\s*([\s\S]+?)\n+\s*A:\s*([\s\S]+?)(?=\n\s*Q:|\n##|$)/gi;
  if (cards.length < 10) {
    while ((m = loose.exec(section)) !== null) {
      cards.push({ q: m[1].trim(), a: m[2].trim() });
    }
  }

  return cards;
}
