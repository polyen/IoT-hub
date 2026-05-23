import { useState, useCallback } from "react";
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
  const t = type.toLowerCase();
  // camera/event stores the detection class in payload.label (not payload.class)
  const label = String(payload?.label ?? "").toLowerCase();
  // event/fused stores the fused event type in payload.event_type
  const eventType = String(payload?.event_type ?? "").toLowerCase();

  // ── camera/event ────────────────────────────────────────────────────────────
  if (t === "camera/event") {
    if (label === "fire")
      return { label: "Вогонь", Icon: Flame, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
    if (label === "smoke")
      return { label: "Дим", Icon: Wind, iconBg: "bg-orange-500/20", iconColor: "text-orange-400", significant: true };
    if (label === "person") {
      const faceId = payload?.face_id as string | undefined;
      if (faceId && faceId !== "unknown")
        return { label: faceId, Icon: User, iconBg: "bg-green-500/20", iconColor: "text-green-400" };
      return { label: "Людина", Icon: User, iconBg: "bg-primary-500/20", iconColor: "text-primary-400" };
    }
    return { label: "Камера", Icon: Camera, iconBg: "bg-primary-500/20", iconColor: "text-primary-400" };
  }

  // ── event/fused ─────────────────────────────────────────────────────────────
  if (t === "event/fused") {
    if (eventType === "fire")
      return { label: "Вогонь", Icon: Flame, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
    if (eventType === "smoke")
      return { label: "Дим", Icon: Wind, iconBg: "bg-orange-500/20", iconColor: "text-orange-400", significant: true };
    if (eventType === "fall" || eventType === "fall_detected")
      return { label: "Падіння", Icon: PersonStanding, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
    if (eventType === "motion")
      return { label: "Рух", Icon: Activity, iconBg: "bg-violet-500/20", iconColor: "text-violet-400" };
    return { label: "Сигнал", Icon: Zap, iconBg: "bg-warm-500/20", iconColor: "text-warm-400" };
  }

  // ── camera/identity ─────────────────────────────────────────────────────────
  if (t === "camera/identity") {
    const identity = (payload?.name ?? payload?.face_id) as string | undefined;
    if (identity && identity !== "unknown")
      return { label: identity, Icon: User, iconBg: "bg-green-500/20", iconColor: "text-green-400" };
    return { label: "Незнайомець", Icon: UserX, iconBg: "bg-orange-500/20", iconColor: "text-orange-400", significant: true };
  }

  // ── sensors & alerts ────────────────────────────────────────────────────────
  if (t === "alert")
    return { label: "Тривога", Icon: Bell, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
  if (t === "gas" || t.includes("mq2"))
    return { label: "Газ / CO", Icon: Gauge, iconBg: "bg-yellow-500/20", iconColor: "text-yellow-400", significant: true };
  if (t.includes("dht") || t.includes("temperature") || t.includes("climate"))
    return { label: "Клімат", Icon: Thermometer, iconBg: "bg-blue-500/20", iconColor: "text-blue-400" };
  if (t.includes("door"))
    return { label: "Двері", Icon: DoorOpen, iconBg: "bg-cyan-500/20", iconColor: "text-cyan-400" };
  if (t.includes("motion") || t.includes("pir"))
    return { label: "Рух", Icon: Activity, iconBg: "bg-violet-500/20", iconColor: "text-violet-400" };
  if (t.includes("security") || t.includes("auth"))
    return { label: "Безпека", Icon: Shield, iconBg: "bg-indigo-500/20", iconColor: "text-indigo-400" };

  return { label: type, Icon: Activity, iconBg: "bg-[color:var(--raised)]", iconColor: "text-[color:var(--text-muted)]" };
}

// ── Payload summary ─────────────────────────────────────────────────────────

function formatPayloadSummary(type: string, payload: Record<string, unknown> | null): string | null {
  if (!payload) return null;

  const t = type.toLowerCase();
  const parts: string[] = [];

  // Confidence
  const conf = payload.confidence ?? payload.conf ?? payload.score;
  if (typeof conf === "number") parts.push(`впевненість ${Math.round(conf * 100)}%`);

  // camera/event: label is the detection class (fire/smoke/person)
  if (t === "camera/event") {
    const faceId = payload.face_id as string | undefined;
    if (faceId && faceId !== "unknown") parts.push(`👤 ${faceId}`);
    const trackId = payload.track_id;
    if (typeof trackId === "number") parts.push(`трек #${trackId}`);
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  // event/fused: show contributing sources
  if (t === "event/fused") {
    const sources = payload.sources;
    if (Array.isArray(sources) && sources.length > 0) {
      const srcLabel: Record<string, string> = { camera: "камера", sensors: "сенсори", audio: "аудіо" };
      parts.push(sources.map((s: unknown) => srcLabel[String(s)] ?? String(s)).join(", "));
    }
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  // camera/identity: show who was seen
  if (t === "camera/identity") {
    const trackId = payload.track_id;
    if (typeof trackId === "number") parts.push(`трек #${trackId}`);
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  // Sensors
  const temp = payload.temperature ?? payload.temp_c;
  const hum = payload.humidity;
  if (typeof temp === "number") parts.push(`${temp.toFixed(1)} °C`);
  if (typeof hum === "number") parts.push(`${hum.toFixed(0)}% RH`);

  const ppm = payload.ppm ?? payload.gas_ppm;
  if (typeof ppm === "number") parts.push(`${ppm.toFixed(0)} ppm`);

  const open = payload.open ?? payload.state;
  if (open === true || open === "open") parts.push("відчинено");
  if (open === false || open === "closed") parts.push("зачинено");

  if (payload.pir === true) parts.push("виявлено рух");

  if (parts.length === 0) {
    const entries = Object.entries(payload)
      .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
      .slice(0, 3);
    for (const [k, v] of entries) parts.push(`${k}: ${v}`);
  }

  return parts.length > 0 ? parts.join(" · ") : null;
}

// ── Inline feedback ─────────────────────────────────────────────────────────

const PRESET_TAGS = ["свічка", "пара", "сонце", "відбиття", "інше"];

function FeedbackRow({ eventId }: { eventId: string }) {
  const [choice, setChoice] = useState<"tp" | "fp" | "not_sure" | null>(null);
  const [tag, setTag] = useState("");
  const [sent, setSent] = useState(false);
  const [sending, setSending] = useState(false);

  const submit = useCallback(
    async (label: "tp" | "fp" | "not_sure") => {
      if (sent || sending) return;
      setChoice(label);
      setSending(true);
      try {
        await fetch("/api/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ alert_id: eventId, user_label: label, tag: tag || undefined }),
        });
        setSent(true);
      } catch {
        setSent(true); // optimistic
      } finally {
        setSending(false);
      }
    },
    [eventId, tag, sent, sending],
  );

  if (sent) return <p className="text-[11px] text-green-400 mt-2">Дякуємо!</p>;

  return (
    <div className="mt-2.5 pt-2.5 border-t border-[color:var(--border)]">
      <div className="flex flex-wrap gap-1.5">
        {(["tp", "fp", "not_sure"] as const).map((l) => (
          <button
            key={l}
            onClick={() => submit(l)}
            disabled={sending}
            className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors disabled:opacity-50 ${
              choice === l
                ? "border-blue-500 bg-blue-900/60 text-blue-200"
                : "border-[color:var(--border)] text-[color:var(--text-muted)] hover:border-slate-400"
            }`}
          >
            {l === "tp" ? "✓ Реальна" : l === "fp" ? "✗ Хибна" : "? Не впевнений"}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-1 mt-1.5">
        {PRESET_TAGS.map((t) => (
          <button
            key={t}
            onClick={() => setTag((prev) => (prev === t ? "" : t))}
            className={`text-[10px] px-2 py-0.5 rounded-full border transition-colors ${
              tag === t
                ? "border-blue-500 bg-blue-900/40 text-blue-300"
                : "border-[color:var(--border)] text-[color:var(--text-faint)] hover:border-slate-500"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Event card ──────────────────────────────────────────────────────────────

function EventCard({ event }: { event: HubEvent }) {
  const meta = getEventMeta(event.type, event.payload);
  const summary = formatPayloadSummary(event.type, event.payload);
  const Icon = meta.Icon;
  const showFeedback =
    meta.significant &&
    (event.type === "camera/event" || event.type === "event/fused" || event.type === "alert");

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

          {showFeedback && <FeedbackRow eventId={event.id} />}
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
