/**
 * Répartit le MRU **facturé client** d’une seule génération de cours (un appel API)
 * entre les blocs markdown « cours », « quiz » et « fiches » pour l’affichage.
 * La somme `cours + quiz + fiches` vaut exactement `totalLessonMru` (à la précision flottante près, corrigée sur le cours).
 *
 * @param {string | null | undefined} lessonMarkdown
 * @param {number} totalLessonMru MRU déjà facturés pour `/generate` (revente client)
 * @returns {{ cours: number; quiz: number; fiches: number }}
 */
export function allocateLessonBilledMru(lessonMarkdown, totalLessonMru) {
  const t = Number(totalLessonMru);
  if (!Number.isFinite(t) || t <= 0) {
    return { cours: 0, quiz: 0, fiches: 0 };
  }

  const md = String(lessonMarkdown || "");
  const idx6 = md.search(/##\s*6\.\s*PRACTICE QUIZ/i);
  const idx7 = md.search(/##\s*7\.\s*FLASHCARDS/i);
  const idx8 = md.search(/##\s*8\.\s*/i);

  const beforeQuiz = idx6 === -1 ? md : md.slice(0, idx6);
  const quizSection = idx6 === -1 ? "" : idx7 === -1 ? md.slice(idx6) : md.slice(idx6, idx7);
  const flashSection = idx7 === -1 ? "" : idx8 === -1 ? md.slice(idx7) : md.slice(idx7, idx8);
  const afterFlash = idx7 !== -1 && idx8 !== -1 ? md.slice(idx8) : "";

  let coursChars = beforeQuiz.length + afterFlash.length;
  let quizChars = quizSection.length;
  let fichesChars = flashSection.length;
  let sumChars = coursChars + quizChars + fichesChars;
  if (sumChars < 1) {
    coursChars = 1;
    quizChars = 0;
    fichesChars = 0;
    sumChars = 1;
  }

  let cours = (t * coursChars) / sumChars;
  let quiz = (t * quizChars) / sumChars;
  let fiches = (t * fichesChars) / sumChars;
  const drift = t - (cours + quiz + fiches);
  cours += drift;
  return { cours, quiz, fiches };
}
