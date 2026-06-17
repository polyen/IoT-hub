import { useTranslation } from "react-i18next";
import { useTheme, PALETTES } from "../../app/providers/ThemeProvider";
import i18n from "i18next";
import { AudioSettings } from "../../features/audio/AudioSettings";

export default function SettingsPage() {
  const { t } = useTranslation("common");
  const { theme, toggle, palette, setPalette } = useTheme();

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

          {/* Colour palette picker */}
          <div className="px-4 py-3 space-y-2">
            <span className="text-sm">Колірна тема</span>
            <div className="flex flex-wrap gap-2 pt-1">
              {PALETTES.map((p) => {
                const isActive = palette === p.id;
                return (
                  <button
                    key={p.id}
                    onClick={() => setPalette(p.id)}
                    aria-pressed={isActive}
                    aria-label={p.label}
                    className={[
                      "flex items-center gap-2 min-h-[44px] px-3 py-2 rounded-lg border text-sm transition-colors",
                      isActive
                        ? "border-primary-500/40 ring-2 ring-offset-1 ring-offset-slate-800 ring-primary-500 bg-primary-500/10 text-[var(--primary)]"
                        : "border-slate-600 light:border-slate-300 text-[var(--text-muted)] hover:border-slate-400 light:hover:border-slate-400",
                    ].join(" ")}
                  >
                    <span
                      className="inline-block w-[28px] h-[28px] rounded-full flex-shrink-0 border border-black/10"
                      style={{ backgroundColor: p.swatch }}
                    />
                    <span>{p.label}</span>
                  </button>
                );
              })}
            </div>
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

        <AudioSettings />

        <div className="bg-slate-800 light:bg-white rounded-xl p-4 border border-slate-700 light:border-slate-200 space-y-1 text-sm text-slate-300 light:text-slate-700">
          <p className="font-semibold text-slate-200 light:text-slate-900">IoT Hub</p>
          <p>Local-first privacy-preserving IoT Hub</p>
          <p>Edge: Raspberry Pi 5 + Hailo-8 NPU</p>
          <p className="text-slate-500">v0.1.0</p>
        </div>
      </div>
    </div>
  );
}
