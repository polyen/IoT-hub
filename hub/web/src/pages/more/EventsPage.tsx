import { useState } from "react";
import { useWebSocket } from "../../hooks/useWebSocket";
import { useTranslation } from "react-i18next";
import {
  Activity,
  Filter,
  Radio,
  Flame,
  Wind,
  User,
  UserX,
  PersonStanding,
  Thermometer,
  DoorOpen,
  Gauge,
  Zap,
  Bell,
  Camera,
  Shield,
  LucideIcon,
} from "lucide-react";
import { tierBgClass } from "../../lib/tier";
import { relativeTime, fullDateTime } from "../../lib/format";
import { EmptyState } from "../../components/EmptyState";
import type { HubEvent } from "../../lib/types";

// ── Event metadata ──────────────────────────────────────────────────────────

interface EventMeta {
  label: string;
  Icon: LucideIcon;
  iconBg: string;
  iconColor: string;
  significant?: boolean;
}

function getEventMeta(type: string, payload: Record<string, unknown> | null): EventMeta {
  // Normalize type to handle slashes and case
  const t = type.toLowerCase();

  if (t === "fire" || (t.includes("camera") && String(payload?.class) === "fire")) {
    return { label: "Вогонь", Icon: Flame, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
  }
  if (t === "smoke" || (t.includes("camera") && String(payload?.class) === "smoke")) {
    return { label: "Дим", Icon: Wind, iconBg: "bg-orange-500/20", iconColor: "text-orange-400", significant: true };
  }
  if (t === "fall" || t === "fall_detected") {
    return { label: "Падіння", Icon: PersonStanding, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
  }
  if (t === "stranger") {
    return { label: "Незнайомець", Icon: UserX, iconBg: "bg-orange-500/20", iconColor: "text-orange-400", significant: true };
  }
  if (t === "alert") {
    return { label: "Тривога", Icon: Bell, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
  }
  if (t.includes("identity") || t === "face_recognized") {
    return { label: "Обличчя розпізнано", Icon: User, iconBg: "bg-green-500/20", iconColor: "text-green-400" };
  }
  if (t === "gas" || t.includes("mq2")) {
    return { label: "Газ / CO", Icon: Gauge, iconBg: "bg-yellow-500/20", iconColor: "text-yellow-400", significant: true };
  }
  if (t.includes("person") || (t.includes("camera") && String(payload?.class) === "person")) {
    return { label: "Людина", Icon: User, iconBg: "bg-primary-500/20", iconColor: "text-primary-400" };
  }
  if (t.includes("camera") || t === "camera/event") {
    return { label: "Камера", Icon: Camera, iconBg: "bg-primary-500/20", iconColor: "text-primary-400" };
  }
  if (t.includes("dht") || t.includes("temperature") || t.includes("sensor/climate")) {
    return { label: "Клімат", Icon: Thermometer, iconBg: "bg-blue-500/20", iconColor: "text-blue-400" };
  }
  if (t.includes("door") || t.includes("sensor/door")) {
    return { label: "Двері", Icon: DoorOpen, iconBg: "bg-cyan-500/20", iconColor: "text-cyan-400" };
  }
  if (t.includes("motion") || t.includes("pir")) {
    return { label: "Рух", Icon: Activity, iconBg: "bg-violet-500/20", iconColor: "text-violet-400" };
  }
  if (t.includes("fused") || t.includes("fusion")) {
    return { label: "Злитий сигнал", Icon: Zap, iconBg: "bg-warm-500/20", iconColor: "text-warm-400" };
  }
  if (t.includes("security") || t.includes("auth")) {
    return { label: "Безпека", Icon: Shield, iconBg: "bg-indigo-500/20", iconColor: "text-indigo-400" };
  }

  return { label: type, Icon: Activity, iconBg: "bg-[color:var(--raised)]", iconColor: "text-[color:var(--text-muted)]" };
}

// ── Payload summary ─────────────────────────────────────────────────────────

function formatPayloadSummary(type: string, payload: Record<string, unknown> | null): string | null {
  if (!payload) return null;

  const t = type.toLowerCase();
  const parts: string[] = [];

  // Confidence / probability
  const conf = payload.confidence ?? payload.conf ?? payload.score;
  if (typeof conf === "number") {
    parts.push(`впевненість ${Math.round(conf * 100)}%`);
  }

  // Identity / name
  const name = payload.name ?? payload.face_id ?? payload.identity;
  if (name && typeof name === "string") {
    parts.push(`👤 ${name}`);
  }

  // Detection class (person/fire/smoke)
  const cls = payload.class ?? payload.cls;
  if (cls && typeof cls === "string" && !t.includes(cls as string)) {
    const clsLabel: Record<string, string> = { person: "людина", fire: "вогонь", smoke: "дим" };
    parts.push(clsLabel[cls] ?? cls);
  }

  // Temperature / humidity
  const temp = payload.temperature ?? payload.temp_c;
  const hum = payload.humidity;
  if (typeof temp === "number") {
    parts.push(`${temp.toFixed(1)} °C`);
  }
  if (typeof hum === "number") {
    parts.push(`${hum.toFixed(0)}% RH`);
  }

  // Gas PPM
  const ppm = payload.ppm ?? payload.gas_ppm;
  if (typeof ppm === "number") {
    parts.push(`${ppm.toFixed(0)} ppm`);
  }

  // Door state
  const open = payload.open ?? payload.state;
  if (open === true || open === "open") parts.push("відчинено");
  if (open === false || open === "closed") parts.push("зачинено");

  // PIR motion
  if (payload.pir === true) parts.push("виявлено рух");

  // Track ID
  const trackId = payload.track_id;
  if (typeof trackId === "number") {
    parts.push(`трек #${trackId}`);
  }

  // Fallback: key→value for small payloads without specific handling
  if (parts.length === 0) {
    const entries = Object.entries(payload)
      .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
      .slice(0, 3);
    for (const [k, v] of entries) {
      parts.push(`${k}: ${v}`);
    }
  }

  return parts.length > 0 ? parts.join(" · ") : null;
}

// ── Event card ──────────────────────────────────────────────────────────────

function EventCard({ event }: { event: HubEvent }) {
  const meta = getEventMeta(event.type, event.payload);
  const summary = formatPayloadSummary(event.type, event.payload);
  const Icon = meta.Icon;

  return (
    <div
      className={`card rounded-xl px-3.5 py-3 transition-all ${
        meta.significant ? "border-l-[3px] border-l-red-500/70" : ""
      }`}
    >
      <div className="flex items-start gap-3">
        {/* Icon badge */}
        <div
          className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 mt-0.5 ${meta.iconBg}`}
        >
          <Icon size={17} strokeWidth={1.8} className={meta.iconColor} />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-[color:var(--text)] leading-snug">
                {meta.label}
              </p>
              {summary && (
                <p className="text-xs text-[color:var(--text-muted)] mt-0.5 leading-relaxed">
                  {summary}
                </p>
              )}
            </div>
            <span
              className="text-xs text-[color:var(--text-faint)] shrink-0 mt-0.5 whitespace-nowrap"
              title={fullDateTime(event.timestamp)}
            >
              {relativeTime(event.timestamp)}
            </span>
          </div>

          {/* Footer: room + tier */}
          <div className="flex items-center gap-2 mt-1.5">
            {event.room && (
              <span className="text-[11px] font-medium text-[color:var(--text-faint)]">
                {event.room}
              </span>
            )}
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold ${tierBgClass(event.tier)}`}
            >
              T{event.tier}
            </span>
            {event.model_version && (
              <span className="text-[10px] text-[color:var(--text-faint)] font-mono truncate">
                {event.model_version}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function EventsPage() {
  const { t } = useTranslation("events");
  const { events, connected, missedCount, clearMissed } = useWebSocket();
  const [filterType, setFilterType] = useState("");
  const [filterRoom, setFilterRoom] = useState("");

  const filtered = events.filter((e: HubEvent) => {
    if (filterType && !e.type.toLowerCase().includes(filterType.toLowerCase())) return false;
    if (filterRoom && e.room !== filterRoom) return false;
    return true;
  });

  const rooms = [...new Set(events.map((e: HubEvent) => e.room).filter(Boolean))] as string[];

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-[color:var(--text)]">{t("title")}</h1>
        <div className="flex items-center gap-1.5">
          <span
            className={`h-2 w-2 rounded-full ${
              connected ? "bg-green-500 animate-pulse-slow" : "bg-[color:var(--text-faint)]"
            }`}
          />
          <span className="text-xs text-[color:var(--text-muted)]">
            {connected ? "live" : "..."}
          </span>
        </div>
      </div>

      {/* Filters */}
      <div className="card rounded-2xl px-4 py-3 flex gap-3 items-center">
        <Filter size={15} className="text-[color:var(--text-faint)] shrink-0" />
        <input
          type="text"
          placeholder={t("filter.placeholder")}
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="flex-1 min-w-0 text-sm bg-transparent outline-none text-[color:var(--text)] placeholder-[color:var(--text-faint)]"
        />
        {rooms.length > 0 && (
          <select
            value={filterRoom}
            onChange={(e) => setFilterRoom(e.target.value)}
            className="text-xs bg-transparent outline-none text-[color:var(--text-muted)] cursor-pointer"
          >
            <option value="">{t("filter.all")}</option>
            {rooms.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        )}
      </div>

      {missedCount > 0 && (
        <div className="flex items-center justify-between card rounded-2xl px-4 py-3 border-primary-500/30 bg-primary-500/5">
          <div className="flex items-center gap-2">
            <Radio size={14} className="text-primary-400" />
            <span className="text-sm text-primary-300">
              {t("missed", { count: missedCount })}
            </span>
          </div>
          <button
            onClick={clearMissed}
            className="text-xs text-[color:var(--text-muted)] hover:text-[color:var(--text)] transition-colors"
          >
            {t("missed_close")}
          </button>
        </div>
      )}

      {filtered.length === 0 ? (
        <EmptyState
          message={t("title") + " — немає"}
          Icon={Activity}
          description="Підключіться до системи, щоб бачити події"
        />
      ) : (
        <div className="space-y-2">
          {filtered.map((event: HubEvent) => (
            <EventCard key={event.id} event={event} />
          ))}
        </div>
      )}
    </div>
  );
}
