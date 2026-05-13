import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

const ITEMS = [
  { to: "/more/events", icon: "≋", key: "events" },
  { to: "/more/scenarios", icon: "⏱", key: "scenarios" },
  { to: "/more/digest", icon: "📋", key: "digest" },
  { to: "/more/devices", icon: "⚙", key: "devices" },
  { to: "/more/security", icon: "⚿", key: "security" },
  { to: "/more/system", icon: "⬡", key: "system" },
  { to: "/more/policy", icon: "📄", key: "policy" },
  { to: "/more/privacy", icon: "🔒", key: "privacy" },
  { to: "/more/settings", icon: "⚙", key: "settings" },
  { to: "/more/about", icon: "ℹ", key: "about" },
] as const;

export default function MoreIndex() {
  const { t } = useTranslation("common");
  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">{t("nav.more")}</h1>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {ITEMS.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className="flex flex-col items-center gap-2 p-4 rounded-xl bg-slate-800 light:bg-white border border-slate-700 light:border-slate-200 hover:border-blue-500 transition-colors"
          >
            <span className="text-2xl">{item.icon}</span>
            <span className="text-sm text-center">{t(`more.${item.key}`)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
