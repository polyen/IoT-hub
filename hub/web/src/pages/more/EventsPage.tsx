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
  const label = String(payload?.label ?? "").toLowerCase();
  const eventType = String(payload?.event_type ?? "").toLowerCase();

  // ── camera/event ────────────────────────────────────────────────────────────
  if (t === "camera/event") {
    if (label === "fire")
      return { label: "Вогонь", Icon: Flame, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
    if (label === "smoke")
      return { label: "Дим", Icon: Wind, iconBg: "bg-orange-500/20", iconColor: "text-orange-400", significant: true };
    if (label === "fall")
      return { label: "Падіння", Icon: PersonStanding, iconBg: "bg-red-500/20", iconColor: "text-red-400", significant: true };
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
    if (eventType === "person")
      return { label: "Людина", Icon: User, iconBg: "bg-primary-500/20", iconColor: "text-primary-400" };
    if (eventType === "motion")
      return { label: "Рух", Icon: Activity, iconBg: "bg-violet-500/20", iconColor: "text-violet-400" };
    if (eventType === "gas")
      return { label: "Газ / CO", Icon: Gauge, iconBg: "bg-yellow-500/20", iconColor: "text-yellow-400", significant: true };
    // Show the raw event_type when not recognised so the user sees something useful
    if (eventType)
      return { label: eventType, Icon: Zap, iconBg: "bg-[color:var(--raised)]", iconColor: "text-[color:var(--text-muted)]" };
    return { label: "Сигнал", Icon: Zap, iconBg: "bg-[color:var(--raised)]", iconColor: "text-[color:var(--text-muted)]" };
  }

  // ── camera/identity ─────────────────────────────────────────────────────────
  if (t === "camera/identity") {
    // CV pipeline publishes field "identity"; fall back to legacy field names
    const identity = (payload?.identity ?? payload?.name ?? payload?.face_id) as string | undefined;
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

const SOURCE_LABELS: Record<string, string> = {
  camera: "камера",
  smoke_sensor: "димовий сенсор",
  pir: "PIR сенсор",
  gas_sensor: "газовий сенсор",
  audio: "мікрофон",
  sensors: "сенсори",
};

function formatPayloadSummary(type: string, payload: Record<string, unknown> | null): string | null {
  if (!payload) return null;

  const t = type.toLowerCase();
  const parts: string[] = [];

  const conf = payload.confidence ?? payload.conf ?? payload.score;
  const confStr = typeof conf === "number" ? `${Math.round(conf * 100)}%` : null;

  // ── camera/event ─────────────────────────────────────────────────────────────
  if (t === "camera/event") {
    const label = String(payload.label ?? "").toLowerCase();
    const faceId = payload.face_id as string | undefined;
    const trackId = payload.track_id;

    if (label === "person") {
      if (faceId && faceId !== "unknown") parts.push(`Ідентифіковано: ${faceId}`);
      else parts.push("Обличчя не розпізнано");
    }
    if (label === "fall") parts.push("виявлено падіння");
    if (confStr) parts.push(`впевненість ${confStr}`);
    if (typeof trackId === "number") parts.push(`трек #${trackId}`);
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  // ── event/fused ───────────────────────────────────────────────────────────────
  if (t === "event/fused") {
    if (confStr) parts.push(`впевненість ${confStr}`);
    const sources = payload.sources;
    if (Array.isArray(sources) && sources.length > 0) {
      const srcStr = sources.map((s: unknown) => SOURCE_LABELS[String(s)] ?? String(s)).join(" + ");
      parts.push(`джерела: ${srcStr}`);
    }
    const pir = payload.pir_adjusted;
    if (pir) parts.push("знижено (без PIR)");
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  // ── camera/identity ───────────────────────────────────────────────────────────
  if (t === "camera/identity") {
    const identity = (payload.identity ?? payload.name ?? payload.face_id) as string | undefined;
    if (identity && identity !== "unknown") {
      parts.push(`Розпізнано: ${identity}`);
    } else {
      parts.push("Невідома особа");
    }
    if (confStr) parts.push(`впевненість ${confStr}`);
    const trackId = payload.track_id;
    if (typeof trackId === "number") parts.push(`трек #${trackId}`);
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  // ── sensors ───────────────────────────────────────────────────────────────────
  const temp = payload.temperature ?? payload.temp_c;
  const hum = payload.humidity;
  if (typeof temp === "number") parts.push(`${temp.toFixed(1)} °C`);
  if (typeof hum === "number") parts.push(`вологість ${hum.toFixed(0)}%`);

  const ppm = payload.ppm ?? payload.gas_ppm;
  if (typeof ppm === "number") parts.push(`${ppm.toFixed(0)} ppm`);

  const open = payload.open ?? payload.state;
  if (open === true || open === "open") parts.push("відчинено");
  if (open === false || open === "closed") parts.push("зачинено");
  if (payload.pir === true) parts.push("рух виявлено");

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

type FeedbackLabel = "tp" | "fp" | "not_sure";

function normalizeFeedback(raw: string | null | undefined): FeedbackLabel | null {
  if (!raw) return null;
  const l = raw.toLowerCase();
  if (l === "tp" || l === "fp" || l === "not_sure") return l;
  return null;
}

function FeedbackRow({
  eventId,
  initialFeedback,
}: {
  eventId: string;
  initialFeedback?: string | null;
}) {
  const [choice, setChoice] = useState<FeedbackLabel | null>(
    () => normalizeFeedback(initialFeedback),
  );
  const [tag, setTag] = useState("");
  const [sent, setSent] = useState(!!initialFeedback);
  const [sending, setSending] = useState(false);

  const submit = useCallback(
    async (label: FeedbackLabel) => {
      if (sending) return;
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
    [eventId, tag, sending],
  );

  if (sent && choice) {
    const labelText =
      choice === "tp" ? "✓ Реальна" : choice === "fp" ? "✗ Хибна" : "? Не впевнений";
    return (
      <div className="mt-2.5 pt-2.5 border-t border-[color:var(--border)] flex items-center gap-2">
        <span className="text-[11px] text-blue-400">{labelText}</span>
        <button
          onClick={() => { setSent(false); setChoice(null); }}
          className="text-[10px] text-[color:var(--text-faint)] hover:text-[color:var(--text-muted)] transition-colors"
        >
          змінити
        </button>
      </div>
    );
  }

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

          {showFeedback && (
            <FeedbackRow eventId={event.id} initialFeedback={event.user_feedback} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Filtering helpers ────────────────────────────────────────────────────────

type CategoryFilter = "all" | "people" | "danger" | "motion" | "sensors";

const CATEGORIES: { value: CategoryFilter; label: string }[] = [
  { value: "all",     label: "Всі" },
  { value: "people",  label: "Люди" },
  { value: "danger",  label: "Небезпека" },
  { value: "motion",  label: "Рух" },
  { value: "sensors", label: "Сенсори" },
];

function matchesCategory(e: HubEvent, cat: CategoryFilter): boolean {
  if (cat === "all") return true;
  const t = e.type.toLowerCase();
  const label = String(e.payload?.label ?? "").toLowerCase();
  const eventType = String(e.payload?.event_type ?? "").toLowerCase();

  if (cat === "people")
    return (t === "camera/event" && label === "person") || t === "camera/identity";
  if (cat === "danger")
    return (
      (t === "camera/event" && (label === "fire" || label === "smoke" || label === "fall")) ||
      (t === "event/fused" && (eventType === "fire" || eventType === "smoke" || eventType === "fall" || eventType === "fall_detected" || eventType === "gas")) ||
      t === "alert" || t.includes("gas") || t.includes("mq2")
    );
  if (cat === "motion")
    return (
      (t === "event/fused" && (eventType === "person" || eventType === "motion")) ||
      t.includes("pir") || t.includes("motion")
    );
  if (cat === "sensors")
    return t === "sensors" || t.includes("dht") || t.includes("temperature") || t.includes("door");
  return true;
}

function getConfidence(e: HubEvent): number | null {
  const c = e.payload?.confidence ?? e.payload?.conf ?? e.payload?.score;
  return typeof c === "number" ? c : null;
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function EventsPage() {
  const { t } = useTranslation("events");
  const { events, connected, missedCount, clearMissed } = useWebSocket();
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [filterRoom, setFilterRoom] = useState("");
  const [confFilter, setConfFilter] = useState(true); // hide < 50 % by default

  const filtered = events.filter((e: HubEvent) => {
    if (!matchesCategory(e, category)) return false;
    if (filterRoom && e.room !== filterRoom) return false;
    if (confFilter) {
      const conf = getConfidence(e);
      // Only apply the threshold when the event actually carries a confidence value
      if (conf !== null && conf < 0.5) return false;
    }
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
      <div className="card rounded-2xl px-4 py-3 space-y-2.5">
        {/* Category chips */}
        <div className="flex items-center gap-2 flex-wrap">
          <Filter size={14} className="text-[color:var(--text-faint)] shrink-0" />
          {CATEGORIES.map((cat) => (
            <button
              key={cat.value}
              onClick={() => setCategory(cat.value)}
              className={`text-xs px-3 py-1 rounded-full border transition-all ${
                category === cat.value
                  ? "border-primary-500/60 bg-primary-500/15 text-primary-300"
                  : "border-[color:var(--border)] text-[color:var(--text-muted)] hover:border-[color:var(--text-faint)]"
              }`}
            >
              {cat.label}
            </button>
          ))}
        </div>

        {/* Second row: confidence toggle + room */}
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <div
              onClick={() => setConfFilter((v) => !v)}
              className={`w-8 h-4 rounded-full transition-colors relative ${
                confFilter ? "bg-primary-600" : "bg-[color:var(--raised)]"
              }`}
            >
              <span
                className={`absolute top-0.5 w-3 h-3 rounded-full bg-white shadow transition-all ${
                  confFilter ? "left-4" : "left-0.5"
                }`}
              />
            </div>
            <span className="text-xs text-[color:var(--text-muted)]">Впевненість &gt; 50%</span>
          </label>

          {rooms.length > 0 && (
            <select
              value={filterRoom}
              onChange={(e) => setFilterRoom(e.target.value)}
              className="ml-auto text-xs bg-transparent outline-none text-[color:var(--text-muted)] cursor-pointer"
            >
              <option value="">Усі кімнати</option>
              {rooms.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          )}
        </div>
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
