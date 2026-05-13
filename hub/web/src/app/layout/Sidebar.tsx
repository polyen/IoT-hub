import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { clsx } from "clsx";
import { ConfirmBadge } from "./ConfirmBadge";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  badge?: boolean;
  exact?: boolean;
}

export function Sidebar() {
  const { t } = useTranslation("common");

  const primary: NavItem[] = [
    { to: "/", label: t("nav.home"), icon: "⌂", exact: true },
    { to: "/cameras", label: t("nav.cameras"), icon: "⬛" },
    { to: "/voice", label: t("nav.voice"), icon: "◎" },
    { to: "/confirm", label: t("nav.confirm"), icon: "✓", badge: true },
  ];

  const secondary: NavItem[] = [
    { to: "/more/events", label: t("more.events"), icon: "≋" },
    { to: "/more/scenarios", label: t("more.scenarios"), icon: "⏱" },
    { to: "/more/digest", label: t("more.digest"), icon: "📋" },
    { to: "/more/devices", label: t("more.devices"), icon: "⚙" },
    { to: "/more/security", label: t("more.security"), icon: "⚿" },
    { to: "/more/system", label: t("more.system"), icon: "⬡" },
    { to: "/more/policy", label: t("more.policy"), icon: "📄" },
    { to: "/more/privacy", label: t("more.privacy"), icon: "🔒" },
    { to: "/more/settings", label: t("more.settings"), icon: "⚙" },
  ];

  const renderItem = (item: NavItem) => (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.exact}
      className={({ isActive }) =>
        clsx(
          "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors relative",
          isActive
            ? "bg-blue-600/20 text-blue-300"
            : "text-slate-400 hover:text-slate-100 hover:bg-slate-700/50 light:text-slate-600 light:hover:text-slate-900 light:hover:bg-slate-100",
        )
      }
    >
      <span className="w-5 text-center text-base relative">
        {item.icon}
        {item.badge && <ConfirmBadge />}
      </span>
      <span>{item.label}</span>
    </NavLink>
  );

  return (
    <aside className="fixed left-0 top-0 bottom-0 w-[220px] bg-slate-900 light:bg-white border-r border-slate-700 light:border-slate-200 flex flex-col z-20">
      <div className="px-4 py-4 border-b border-slate-700 light:border-slate-200">
        <span className="font-bold text-blue-400 text-lg">IoT Hub</span>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3 flex flex-col gap-0.5">
        {primary.map(renderItem)}
        <div className="my-2 border-t border-slate-700 light:border-slate-200" />
        {secondary.map(renderItem)}
      </nav>
    </aside>
  );
}
