/**
 * Répartit le MRU facturé entre cours, quiz et fiches.
 * Version ultra-robuste pour éviter tout crash UI.
 */
export function allocateLessonBilledMru(lessonMarkdown, totalLessonMru) {
  const t = Number(totalLessonMru) || 0;
  if (t <= 0) return { cours: 0, quiz: 0, fiches: 0 };

  const md = String(lessonMarkdown || "");
  // Regex élargie pour capturer plus de variantes de titres générés par l'IA
  const reQuiz = /##\s+(?:practice\s*quiz|quiz|qcm|questions?\s*(?:de\s*r[ée]vision|d'auto-?[\s\w]*)|اختبار|امتحان|أسئلة)/i;
  const reFlash = /##\s+(?:flashcards?|fiches?|cartes?\s*m[ée]moire|glossaire|lexique|بطاقات|فلاش\s*كارد)/i;
  const reNext = /##\s+/g;

  const idxQuiz = md.search(reQuiz);
  let idxFlash = -1;
  if (idxQuiz !== -1) {
    const afterQuiz = md.slice(idxQuiz + 5); // saute au moins "## Q"
    const f = afterQuiz.search(reFlash);
    if (f !== -1) idxFlash = idxQuiz + 5 + f;
  } else {
    idxFlash = md.search(reFlash);
  }

  let idxAfter = -1;
  if (idxFlash !== -1) {
    reNext.lastIndex = idxFlash + 5;
    const m = reNext.exec(md);
    if (m) idxAfter = m.index;
  }

  const section1 = idxQuiz === -1 ? md : md.slice(0, idxQuiz);
  const section2 = (idxQuiz !== -1) ? (idxFlash !== -1 ? md.slice(idxQuiz, idxFlash) : md.slice(idxQuiz)) : "";
  const section3 = (idxFlash !== -1) ? (idxAfter !== -1 ? md.slice(idxFlash, idxAfter) : md.slice(idxFlash)) : "";
  const section4 = (idxAfter !== -1) ? md.slice(idxAfter) : "";

  let cChars = section1.length + section4.length;
  let qChars = section2.length;
  let fChars = section3.length;
  let total = cChars + qChars + fChars;

  if (total < 1) {
    return { cours: t, quiz: 0, fiches: 0 };
  }

  let cours = (t * cChars) / total;
  let quiz = (t * qChars) / total;
  let fiches = (t * fChars) / total;
  const drift = t - (cours + quiz + fiches);
  cours += drift;

  return { cours, quiz, fiches };
}
