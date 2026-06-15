import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { clsx } from "clsx";
import {
  Home,
  Camera,
  Bot,
  Activity,
  FileText,
  Cpu,
  Shield,
  Server,
  ScrollText,
  Lock,
  Settings,
  Brain,
  LucideIcon,
} from "lucide-react";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  exact?: boolean;
}

function NavItemLink({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.exact}
      className={({ isActive }) =>
        clsx(
          "flex items-center gap-3 px-3 py-2 rounded-xl text-sm transition-all duration-150 relative group",
          isActive
            ? "bg-primary-600/15 text-primary-300 font-medium"
            : "text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)]",
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 bg-primary-400 rounded-r-full" />
          )}
          <Icon
            size={17}
            strokeWidth={isActive ? 2.2 : 1.8}
            className="shrink-0"
          />
          <span className="truncate relative">
            {item.label}
          </span>
        </>
      )}
    </NavLink>
  );
}

function SectionLabel({ label }: { label: string }) {
  return (
    <p className="px-3 pt-5 pb-1 text-[9px] font-mono font-medium uppercase tracking-[0.18em] text-[color:var(--text-faint)]">
      {label}
    </p>
  );
}

export function Sidebar() {
  const { t } = useTranslation("common");

  const primary: NavItem[] = [
    { to: "/", icon: Home, label: t("nav.home"), exact: true },
    { to: "/cameras", icon: Camera, label: t("nav.cameras") },
    { to: "/assistant", icon: Bot, label: t("nav.assistant") },
    { to: "/events", icon: Activity, label: t("nav.events") },
  ];

  const home: NavItem[] = [
    { to: "/more/digest", icon: FileText, label: t("more.digest") },
    { to: "/more/devices", icon: Cpu, label: t("more.devices") },
    { to: "/more/security", icon: Shield, label: t("more.security") },
    { to: "/more/privacy", icon: Lock, label: t("more.privacy") },
    { to: "/more/settings", icon: Settings, label: t("more.settings") },
  ];

  const operator: NavItem[] = [
    { to: "/more/models", icon: Brain, label: t("more.models") },
    { to: "/more/policy", icon: ScrollText, label: t("more.policy") },
    { to: "/more/system", icon: Server, label: t("more.system") },
  ];

  return (
    <aside
      className="fixed left-0 top-0 bottom-0 z-20 flex flex-col"
      style={{
        width: "var(--sidebar-w)",
        background: "var(--card)",
        borderRight: "1px solid var(--border)",
      }}
    >
      {/* Brand */}
      <div className="px-5 py-5 flex items-center gap-3">
        <div
          className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
          style={{
            background: "var(--primary-dim)",
            border: "1px solid rgba(201,168,76,0.3)",
          }}
        >
          <Home size={15} strokeWidth={1.5} className="text-primary-400" />
        </div>
        <div>
          <p className="font-display font-semibold text-sm leading-none tracking-wide text-[color:var(--text)]">
            IoT Hub
          </p>
          <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[color:var(--text-faint)] mt-0.5">
            Smart Home
          </p>
        </div>
      </div>

      <div
        className="mx-4 mb-1"
        style={{ height: "1px", background: "var(--border-subtle)" }}
      />

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
        {primary.map((item) => (
          <NavItemLink key={item.to} item={item} />
        ))}

        <SectionLabel label="Дім" />
        {home.map((item) => (
          <NavItemLink key={item.to} item={item} />
        ))}

        <SectionLabel label="Оператор" />
        {operator.map((item) => (
          <NavItemLink key={item.to} item={item} />
        ))}
      </nav>
    </aside>
  );
}
