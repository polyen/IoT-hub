import { Link } from "react-router-dom";
import {
  Flame,
  Wind,
  PersonStanding,
  UserX,
  Gauge,
  Droplets,
  Bell,
  ShieldCheck,
  ChevronRight,
  Camera,
  ListFilter,
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
  severity: "critical" | "warning";
  why: string;
  cta: { label: string; to: string };
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
  // `alert` events carry the type in `alert_type` (sensors/Zigbee) or
  // `event_type` (CV) — see EventsPage.getEventMeta.
  const at = String(e.payload?.alert_type ?? "").toLowerCase();

  if (label === "fire" || et === "fire")
    return {
      label: "Вогонь",
      Icon: Flame,
      color: "text-red-400",
      severity: "critical",
      why: "Можлива пожежа — перевірте приміщення",
      cta: { label: "Камера", to: "/cameras" },
    };
  if (label === "smoke" || et === "smoke")
    return {
      label: "Дим",
      Icon: Wind,
      color: "text-orange-400",
      severity: "critical",
      why: "Виявлено дим",
      cta: { label: "Камера", to: "/cameras" },
    };
  if (label === "fall" || et === "fall" || et === "fall_detected" || at === "fall")
    return {
      label: "Падіння",
      Icon: PersonStanding,
      color: "text-red-400",
      severity: "critical",
      why: "Людина впала — потрібна допомога",
      cta: { label: "Камера", to: "/cameras" },
    };
  if (et === "gas" || at === "gas" || t.includes("gas") || t.includes("mq2"))
    return {
      label: "Газ / CO",
      Icon: Gauge,
      color: "text-yellow-400",
      severity: "critical",
      why: "Підвищений рівень газу",
      cta: { label: "Камера", to: "/cameras" },
    };
  if (at === "water_leak")
    return {
      label: "Протікання води",
      Icon: Droplets,
      color: "text-blue-400",
      severity: "warning",
      why: "Ризик затоплення",
      cta: { label: "Деталі", to: "/events" },
    };
  if (t === "alert") {
    // Door + legacy motion (pre-`presence`-topic) are routine — full feed only,
    // not "needs attention". New motion arrives as the `presence` type (not
    // matched here at all). water_leak/fall/gas/unknown stay significant.
    if (["motion", "presence", "occupancy", "door_open", "door_close"].includes(at)) return null;
    return {
      label: "Тривога",
      Icon: Bell,
      color: "text-red-400",
      severity: "critical",
      why: "Спрацювала тривога",
      cta: { label: "Деталі", to: "/events" },
    };
  }
  if (t === "camera/identity") {
    const id = (e.payload?.identity ?? e.payload?.name ?? e.payload?.face_id) as string | undefined;
    if (!id || id === "unknown")
      return {
        label: "Незнайомець",
        Icon: UserX,
        color: "text-orange-400",
        severity: "warning",
        why: "Незнайоме обличчя біля камери",
        cta: { label: "Камера", to: "/cameras" },
      };
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
            const borderClass =
              meta.severity === "critical"
                ? "border-l-red-500/60"
                : "border-l-amber-500/60";
            const CtaIcon = meta.cta.to === "/cameras" ? Camera : ListFilter;
            return (
              <div
                key={e.id}
                className={`card rounded-2xl border-l-[3px] ${borderClass} px-3.5 py-3`}
              >
                {/* Row 1: WHAT — icon + label + room | tier badge + time */}
                <div className="flex items-center gap-3">
                  <Icon size={18} strokeWidth={1.9} className={`shrink-0 ${meta.color}`} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-[color:var(--text)]">
                      {meta.label}
                      {e.room && (
                        <span className="ml-1.5 font-normal text-[color:var(--text-faint)]">
                          · {e.room}
                        </span>
                      )}
                    </p>
                  </div>
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-xs font-semibold ${tierBgClass(e.tier)}`}
                  >
                    T{e.tier}
                  </span>
                  <span className="shrink-0 whitespace-nowrap text-xs text-[color:var(--text-faint)]">
                    {relativeTime(e.timestamp)}
                  </span>
                </div>

                {/* Row 2: WHY */}
                <p className="mt-0.5 pl-[calc(18px+0.75rem)] text-xs text-[color:var(--text-muted)]">
                  {meta.why}
                </p>

                {/* Row 3: WHAT TO DO */}
                <div className="mt-2 pl-[calc(18px+0.75rem)]">
                  <Link
                    to={meta.cta.to}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-[color:var(--border)] bg-[color:var(--raised)] px-3 py-1.5 text-xs font-medium text-[color:var(--text)] transition-colors hover:border-[color:var(--primary)] hover:text-[color:var(--primary)] min-h-[36px]"
                  >
                    <CtaIcon size={13} strokeWidth={2} />
                    {meta.cta.label}
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
