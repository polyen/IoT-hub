import { Suspense } from "react";
import i18n from "i18next";
import { initReactI18next, I18nextProvider } from "react-i18next";
import { setLocale } from "../../lib/format";

import ukCommon from "../../features/i18n/locales/uk/common.json";
import ukEvents from "../../features/i18n/locales/uk/events.json";
import ukSystem from "../../features/i18n/locales/uk/system.json";
import ukVoice from "../../features/i18n/locales/uk/voice.json";
import ukPolicy from "../../features/i18n/locales/uk/policy.json";
import enCommon from "../../features/i18n/locales/en/common.json";
import enEvents from "../../features/i18n/locales/en/events.json";
import enSystem from "../../features/i18n/locales/en/system.json";
import enVoice from "../../features/i18n/locales/en/voice.json";
import enPolicy from "../../features/i18n/locales/en/policy.json";

const savedLang = localStorage.getItem("lang") ?? "uk";

i18n.use(initReactI18next).init({
  lng: savedLang,
  fallbackLng: "uk",
  resources: {
    uk: { common: ukCommon, events: ukEvents, system: ukSystem, voice: ukVoice, policy: ukPolicy },
    en: { common: enCommon, events: enEvents, system: enSystem, voice: enVoice, policy: enPolicy },
  },
  ns: ["common", "events", "system", "voice", "policy"],
  defaultNS: "common",
  interpolation: { escapeValue: false },
});

setLocale(savedLang);

i18n.on("languageChanged", (lng) => {
  setLocale(lng);
  localStorage.setItem("lang", lng);
});

export function I18nProvider({ children }: { children: React.ReactNode }) {
  return (
    <I18nextProvider i18n={i18n}>
      <Suspense fallback={null}>{children}</Suspense>
    </I18nextProvider>
  );
}
