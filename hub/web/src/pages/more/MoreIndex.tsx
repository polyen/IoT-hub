import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Activity,
  FileText,
  Cpu,
  Shield,
  Server,
  ScrollText,
  Lock,
  Settings,
  Brain,
  Thermometer,
  ChevronRight,
  LucideIcon,
} from "lucide-react";

interface MoreItem {
  to: string;
  icon: LucideIcon;
  key: string;
  color: string;
  bgColor: string;
}

interface MoreSection {
  label: string;
  items: MoreItem[];
}

const SECTIONS: MoreSection[] = [
  {
    label: "Дім",
    items: [
      {
        to: "/events",
        icon: Activity,
        key: "events",
        color: "text-sky-400",
        bgColor: "bg-sky-500/15",
      },
      {
        to: "/more/digest",
        icon: FileText,
        key: "digest",
        color: "text-violet-400",
        bgColor: "bg-violet-500/15",
      },
      {
        to: "/more/climate",
        icon: Thermometer,
        key: "climate",
        color: "text-orange-400",
        bgColor: "bg-orange-500/15",
      },
      {
        to: "/more/devices",
        icon: Cpu,
        key: "devices",
        color: "text-cyan-400",
        bgColor: "bg-cyan-500/15",
      },
      {
        to: "/more/security",
        icon: Shield,
        key: "security",
        color: "text-green-400",
        bgColor: "bg-green-500/15",
      },
      {
        to: "/more/privacy",
        icon: Lock,
        key: "privacy",
        color: "text-indigo-400",
        bgColor: "bg-indigo-500/15",
      },
      {
        to: "/more/settings",
        icon: Settings,
        key: "settings",
        color: "text-[color:var(--text-muted)]",
        bgColor: "bg-[color:var(--raised)]",
      },
    ],
  },
  {
    label: "Оператор · розширене",
    items: [
      {
        to: "/more/models",
        icon: Brain,
        key: "models",
        color: "text-pink-400",
        bgColor: "bg-pink-500/15",
      },
      {
        to: "/more/policy",
        icon: ScrollText,
        key: "policy",
        color: "text-slate-400",
        bgColor: "bg-slate-500/15",
      },
      {
        to: "/more/system",
        icon: Server,
        key: "system",
        color: "text-orange-400",
        bgColor: "bg-orange-500/15",
      },
    ],
  },
];

export default function MoreIndex() {
  const { t } = useTranslation("common");

  return (
    <div className="space-y-6 animate-fade-in">
      <h1 className="text-2xl font-bold text-[color:var(--text)]">{t("nav.more")}</h1>

      {SECTIONS.map((section) => (
        <section key={section.label}>
          <p className="text-xs font-semibold uppercase tracking-widest text-[color:var(--text-faint)] mb-3 px-1">
            {section.label}
          </p>
          <div className="card overflow-hidden divide-y divide-[color:var(--border)]">
            {section.items.map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className="flex items-center gap-3.5 px-4 py-3.5 hover:bg-[color:var(--raised)] transition-colors"
                >
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${item.bgColor}`}>
                    <Icon size={18} strokeWidth={1.8} className={item.color} />
                  </div>
                  <span className="flex-1 text-sm font-medium text-[color:var(--text)]">
                    {t(`more.${item.key}`)}
                  </span>
                  <ChevronRight size={15} className="text-[color:var(--text-faint)]" />
                </Link>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
