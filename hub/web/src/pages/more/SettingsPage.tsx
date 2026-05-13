import { useTranslation } from "react-i18next";
import { useTheme } from "../../app/providers/ThemeProvider";
import i18n from "i18next";

export default function SettingsPage() {
  const { t } = useTranslation("common");
  const { theme, toggle } = useTheme();

  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">{t("more.settings")}</h1>
      <div className="space-y-4">
        <div className="bg-slate-800 light:bg-white rounded-xl border border-slate-700 light:border-slate-200 divide-y divide-slate-700 light:divide-slate-200">
          <div className="flex items-center justify-between px-4 py-3">
            <span className="text-sm">Тема</span>
            <button
              onClick={toggle}
              className="text-sm text-blue-400 hover:text-blue-300"
            >
              {theme === "dark" ? "🌙 Темна" : "☀ Світла"}
            </button>
          </div>
          <div className="flex items-center justify-between px-4 py-3">
            <span className="text-sm">Мова / Language</span>
            <div className="flex gap-2">
              {["uk", "en"].map((lng) => (
                <button
                  key={lng}
                  onClick={() => i18n.changeLanguage(lng)}
                  className={`text-sm px-2 py-1 rounded ${i18n.language === lng ? "bg-blue-600 text-white" : "text-slate-400 hover:text-white"}`}
                >
                  {lng === "uk" ? "🇺🇦 UK" : "🇬🇧 EN"}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between px-4 py-3">
            <span className="text-sm">Push-сповіщення</span>
            <button
              onClick={() => Notification.requestPermission()}
              className="text-sm text-blue-400 hover:text-blue-300"
            >
              {Notification.permission === "granted" ? "✓ Дозволено" : "Дозволити"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
