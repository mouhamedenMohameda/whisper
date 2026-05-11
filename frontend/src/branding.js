/**
 * Noms d’engines présentés à l’utilisateur (aucune référence aux fournisseurs techniques tiers).
 */
export const ENGINE_TRANSCRIPTION = "EchoScribe";
export const ENGINE_COURSE = "CourseMind";
/** Transcription hébergée (mode « nuage » dans l’UI). */
export const ENGINE_TRANSCRIBE_CLOUD = "EchoScribe Nuage";
/** Transcription sur ton déploiement (mode « atelier »). */
export const ENGINE_TRANSCRIBE_ATELIER = "EchoScribe Atelier (moins cher)";
/** Harmonisation des passages multilingues (post-transcription). */
export const ENGINE_BRIDGE = "Alliage";
/** Aide à la lecture : sujet + synthèse condensée (optionnel). */
export const ENGINE_INSIGHT = "LecturaSynth";

/** Identifiant wa.me (chiffres seuls, sans +) pour le support admin — +33 6 56 69 69 74. */
export const SUPPORT_WHATSAPP_WA_ID = "33656696974";

/** URL ouverte dans un nouvel onglet pour contacter l’admin sur WhatsApp. */
export const SUPPORT_WHATSAPP_HREF = `https://wa.me/${SUPPORT_WHATSAPP_WA_ID}?text=${encodeURIComponent("Bonjour, j’ai une question sur LecturAI.")}`;
