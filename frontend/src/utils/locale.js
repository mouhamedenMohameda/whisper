/** BCP47 tag pour dates/nombres côté client. */
/** @param {string | undefined} lng */
export function appLocaleTag(lng) {
  return lng?.startsWith("ar") ? "ar-MR" : "fr-FR";
}
