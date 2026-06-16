import { useMemo } from "react";
import { ShieldCheck, Users, AlertTriangle, Camera, Activity } from "lucide-react";

type Mood = "alarm" | "active" | "calm";

interface HomeStatusProps {
  /** Display names of rooms with an active alert. */
  alertRooms: string[];
  /** Display names of rooms with detected presence. */
  presenceRooms: string[];
  camerasOnline: number | string;
  eventsToday: number | string;
}

interface Daypart {
  greeting: string;
  /** Ambient gradient applied behind the hero — shifts morning → night. */
  gradient: string;
}

/** Time-of-day ambient: warm at dawn/dusk, cool-dim at night. */
function daypart(hour: number): Daypart {
  if (hour >= 5 && hour < 11)
    return {
      greeting: "Доброго ранку",
      gradient: "linear-gradient(135deg, rgba(245,200,120,0.20), rgba(217,119,6,0.10) 60%, transparent)",
    };
  if (hour >= 11 && hour < 17)
    return {
      greeting: "Доброго дня",
      gradient: "linear-gradient(135deg, rgba(251,191,36,0.16), rgba(56,189,248,0.08) 70%, transparent)",
    };
  if (hour >= 17 && hour < 22)
    return {
      greeting: "Добрий вечір",
      gradient: "linear-gradient(135deg, rgba(217,119,6,0.22), rgba(190,80,40,0.12) 60%, transparent)",
    };
  return {
    greeting: "Доброї ночі",
    gradient: "linear-gradient(135deg, rgba(99,102,241,0.14), rgba(30,41,59,0.10) 60%, transparent)",
  };
}

const MOOD_STYLE: Record<
  Mood,
  { ring: string; iconBg: string; iconFg: string; Icon: typeof ShieldCheck }
> = {
  alarm: { ring: "ring-2 ring-red-500/60", iconBg: "bg-red-500/20", iconFg: "text-red-400", Icon: AlertTriangle },
  active: { ring: "ring-1 ring-primary-500/30", iconBg: "bg-primary-500/20", iconFg: "text-primary-400", Icon: Users },
  calm: { ring: "ring-1 ring-emerald-500/25", iconBg: "bg-emerald-500/15", iconFg: "text-emerald-400", Icon: ShieldCheck },
};

function MiniStat({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 text-xs text-[color:var(--text-muted)]">
      <span className="text-[color:var(--text-faint)]">{icon}</span>
      <span className="font-mono tabular-nums font-semibold text-[color:var(--text)]">{value}</span>
      <span>{label}</span>
    </div>
  );
}

export function HomeStatus({ alertRooms, presenceRooms, camerasOnline, eventsToday }: HomeStatusProps) {
  const mood: Mood = alertRooms.length > 0 ? "alarm" : presenceRooms.length > 0 ? "active" : "calm";
  const dp = useMemo(() => daypart(new Date().getHours()), []);
  const s = MOOD_STYLE[mood];
  const Icon = s.Icon;

  const title =
    mood === "alarm"
      ? "Потрібна увага"
      : mood === "active"
        ? `${dp.greeting} · вдома`
        : `${dp.greeting} · все спокійно`;

  const subtitle =
    mood === "alarm"
      ? `Тривога: ${alertRooms.join(", ")}`
      : mood === "active"
        ? `Активність: ${presenceRooms.join(", ")}`
        : "Жодних значущих подій";

  return (
    <div
      className={`card relative overflow-hidden rounded-2xl px-5 py-5 ${s.ring} ${
        mood === "alarm" ? "animate-pulse-slow" : ""
      }`}
    >
      {/* Ambient time-of-day wash (suppressed during an alarm so red dominates) */}
      {mood !== "alarm" && (
        <div className="pointer-events-none absolute inset-0" style={{ background: dp.gradient }} />
      )}
      <div className="relative flex items-center gap-4">
        <div className={`grid h-14 w-14 shrink-0 place-items-center rounded-2xl ${s.iconBg}`}>
          <Icon size={26} strokeWidth={1.8} className={s.iconFg} />
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-xl font-semibold leading-tight text-[color:var(--text)]">
            {title}
          </h1>
          <p className="mt-0.5 truncate text-sm text-[color:var(--text-muted)]">{subtitle}</p>
        </div>
      </div>

      {/* Demoted metrics — context, not the headline */}
      <div className="relative mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-[color:var(--border-subtle)] pt-3">
        <MiniStat icon={<Users size={13} />} label="присутніх" value={presenceRooms.length} />
        <MiniStat icon={<Camera size={13} />} label="камер онлайн" value={camerasOnline} />
        <MiniStat icon={<Activity size={13} />} label="подій сьогодні" value={eventsToday} />
      </div>
    </div>
  );
}
