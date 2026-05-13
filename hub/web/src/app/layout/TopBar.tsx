import { useTranslation } from "react-i18next";
import { useTheme } from "../providers/ThemeProvider";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";

export function TopBar() {
  const { t } = useTranslation("common");
  const { theme, toggle } = useTheme();
  const isOnline = useOnlineStatus();

  return (
    <header className="h-14 flex items-center justify-between px-4 bg-slate-900 light:bg-white border-b border-slate-700 light:border-slate-200 fixed top-0 left-[220px] right-0 z-20">
      <span className="text-sm text-slate-400 light:text-slate-500">
        <span
          className={`inline-block w-2 h-2 rounded-full mr-1.5 ${isOnline ? "bg-green-500" : "bg-amber-500"}`}
        />
        {isOnline ? t("status.online") : t("status.offline")}
      </span>
      <button
        onClick={toggle}
        className="text-slate-400 hover:text-slate-100 light:text-slate-500 light:hover:text-slate-900 text-sm transition-colors"
        title="Toggle theme"
      >
        {theme === "dark" ? "☀" : "🌙"}
      </button>
    </header>
  );
}
