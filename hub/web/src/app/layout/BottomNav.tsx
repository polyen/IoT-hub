import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Home, LayoutGrid, Sparkles, Camera, Bot, LucideIcon } from "lucide-react";
import { clsx } from "clsx";

interface NavItem {
  to: string;
  icon: LucideIcon;
  key: string;
  exact?: boolean;
  badge?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", icon: Home, key: "home", exact: true },
  { to: "/rooms", icon: LayoutGrid, key: "rooms" },
  { to: "/scenes", icon: Sparkles, key: "scenes" },
  { to: "/cameras", icon: Camera, key: "cameras" },
  { to: "/assistant", icon: Bot, key: "assistant" },
];

export function BottomNav() {
  const { t } = useTranslation("common");

  return (
    <nav
      className="fixed bottom-4 inset-x-0 z-30 flex justify-center px-4 safe-pb"
      style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
    >
      <div className="glass-card flex items-center gap-1 rounded-2xl px-2 py-2 shadow-glass">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.exact}
              className={({ isActive }) =>
                clsx(
                  "relative flex flex-col items-center gap-0.5 rounded-xl px-3 py-2 transition-all duration-200",
                  isActive
                    ? "bg-primary-600/20 text-primary-400"
                    : "text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)]",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span className="relative">
                    <Icon size={22} strokeWidth={isActive ? 2.2 : 1.8} />
                  </span>
                  <span
                    className={clsx(
                      "text-xs font-medium leading-none",
                      isActive ? "text-primary-400" : "text-[color:var(--text-muted)]",
                    )}
                  >
                    {t(`nav.${item.key}`)}
                  </span>
                </>
              )}
            </NavLink>
          );
        })}
      </div>
    </nav>
  );
}
