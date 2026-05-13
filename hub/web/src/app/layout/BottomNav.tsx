import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { clsx } from "clsx";
import { ConfirmBadge } from "./ConfirmBadge";

const ICONS = {
  home: "⌂",
  cameras: "⬛",
  voice: "◎",
  confirm: "✓",
  more: "≡",
};

export function BottomNav() {
  const { t } = useTranslation("common");

  const navItems = [
    { to: "/", label: t("nav.home"), icon: ICONS.home, exact: true },
    { to: "/cameras", label: t("nav.cameras"), icon: ICONS.cameras },
    { to: "/voice", label: t("nav.voice"), icon: ICONS.voice },
    { to: "/confirm", label: t("nav.confirm"), icon: ICONS.confirm, badge: true },
    { to: "/more", label: t("nav.more"), icon: ICONS.more },
  ];

  return (
    <nav className="fixed bottom-0 inset-x-0 z-30 bg-slate-900 light:bg-white border-t border-slate-700 light:border-slate-200 flex safe-pb">
      {navItems.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.exact}
          className={({ isActive }) =>
            clsx(
              "flex-1 flex flex-col items-center justify-center gap-0.5 py-2 text-[10px] transition-colors relative",
              isActive
                ? "text-blue-400"
                : "text-slate-400 hover:text-slate-200 light:text-slate-500 light:hover:text-slate-800",
            )
          }
        >
          {({ isActive }) => (
            <>
              <span className="relative text-xl leading-none">
                {item.icon}
                {item.badge && <ConfirmBadge />}
              </span>
              <span className={clsx("font-medium", isActive && "text-blue-400")}>{item.label}</span>
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
