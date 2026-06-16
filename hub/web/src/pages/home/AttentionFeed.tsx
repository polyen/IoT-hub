import { Link } from "react-router-dom";
import {
  Flame,
  Wind,
  PersonStanding,
  UserX,
  Gauge,
  Bell,
  ShieldCheck,
  ChevronRight,
  LucideIcon,
} from "lucide-react";
import { useWebSocket } from "../../hooks/useWebSocket";
import { relativeTime } from "../../lib/format";
import { tierBgClass } from "../../lib/tier";
import type { HubEvent } from "../../lib/types";

interface AttnMeta {
  label: string;
  Icon: LucideIcon;
  color: string;
}

/**
 * Significance filter — mirrors EventsPage `getEventMeta.significant`, but kept
 * standalone so Home only ever surfaces what genuinely needs attention. The
 * backend already de-dups identity/alert spam, so this is a thin presentation
 * gate, not the primary noise control.
 */
function attentionMeta(e: HubEvent): AttnMeta | null {
  const t = e.type.toLowerCase();
  const label = String(e.payload?.label ?? "").toLowerCase();
  const et = String(e.payload?.event_type ?? "").toLowerCase();

  if (label === "fire" || et === "fire")
    return { label: "Вогонь", Icon: Flame, color: "text-red-400" };
  if (label === "smoke" || et === "smoke")
    return { label: "Дим", Icon: Wind, color: "text-orange-400" };
  if (label === "fall" || et === "fall" || et === "fall_detected")
    return { label: "Падіння", Icon: PersonStanding, color: "text-red-400" };
  if (et === "gas" || t.includes("gas") || t.includes("mq2"))
    return { label: "Газ / CO", Icon: Gauge, color: "text-yellow-400" };
  if (t === "alert") return { label: "Тривога", Icon: Bell, color: "text-red-400" };
  if (t === "camera/identity") {
    const id = (e.payload?.identity ?? e.payload?.name ?? e.payload?.face_id) as string | undefined;
    if (!id || id === "unknown")
      return { label: "Незнайомець", Icon: UserX, color: "text-orange-400" };
  }
  return null;
}

const MAX_ROWS = 4;

export function AttentionFeed() {
  const { events } = useWebSocket();

  const items = events
    .map((e) => ({ e, meta: attentionMeta(e) }))
    .filter((x): x is { e: HubEvent; meta: AttnMeta } => x.meta !== null)
    .slice(0, MAX_ROWS);

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between px-1">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[color:var(--text-faint)]">
          Потребує уваги
        </h2>
        <Link
          to="/events"
          className="flex items-center gap-0.5 text-xs text-[color:var(--text-muted)] hover:text-[color:var(--text)] transition-colors"
        >
          Усі події <ChevronRight size={13} />
        </Link>
      </div>

      {items.length === 0 ? (
        <div className="card flex items-center gap-3 rounded-2xl px-4 py-4">
          <ShieldCheck size={18} className="text-emerald-400" />
          <p className="text-sm text-[color:var(--text-muted)]">
            Все спокійно — значущих подій немає
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(({ e, meta }) => {
            const Icon = meta.Icon;
            return (
              <div
                key={e.id}
                className="card flex items-center gap-3 rounded-2xl border-l-[3px] border-l-red-500/60 px-3.5 py-3"
              >
                <Icon size={18} strokeWidth={1.9} className={meta.color} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-[color:var(--text)]">{meta.label}</p>
                  {e.room && (
                    <p className="text-xs text-[color:var(--text-faint)]">{e.room}</p>
                  )}
                </div>
                <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${tierBgClass(e.tier)}`}>
                  T{e.tier}
                </span>
                <span className="shrink-0 whitespace-nowrap text-xs text-[color:var(--text-faint)]">
                  {relativeTime(e.timestamp)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
