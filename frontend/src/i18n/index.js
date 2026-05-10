import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import fr from "../locales/fr.json";
import ar from "../locales/ar.json";

const STORAGE_KEY = "lecturai-lang";

function readStoredLng() {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    if (s === "ar" || s === "fr") return s;
  } catch {
    /* ignore */
  }
  return "fr";
}

function applyDomLanguage(lng) {
  const isAr = lng === "ar" || lng.startsWith("ar");
  document.documentElement.lang = isAr ? "ar" : "fr";
  document.documentElement.dir = isAr ? "rtl" : "ltr";
}

i18n.on("languageChanged", (lng) => {
  try {
    localStorage.setItem(STORAGE_KEY, lng.split("-")[0] === "ar" ? "ar" : lng === "fr" ? "fr" : lng.startsWith("ar") ? "ar" : "fr");
  } catch {
    /* ignore */
  }
  applyDomLanguage(lng);
});

i18n.use(initReactI18next).init({
  resources: {
    fr: { translation: fr },
    ar: { translation: ar },
  },
  lng: readStoredLng(),
  fallbackLng: "fr",
  interpolation: { escapeValue: false },
});

applyDomLanguage(i18n.language);

export default i18n;
