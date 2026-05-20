import { useState } from "react";
import { useWebSocket } from "../../hooks/useWebSocket";
import { useTranslation } from "react-i18next";
import { Activity, Filter, Radio } from "lucide-react";
import { tierBgClass } from "../../lib/tier";
import { shortDateTime } from "../../lib/format";
import { EmptyState } from "../../components/EmptyState";
import type { HubEvent } from "../../lib/types";

const SIGNIFICANT_TYPES = new Set(["alert", "camera/event", "stranger", "fall", "gas"]);

const TYPE_COLORS: Record<string, string> = {
  alert: "border-l-red-500",
  "camera/event": "border-l-warm-500",
  stranger: "border-l-orange-500",
  fall: "border-l-red-500",
  gas: "border-l-red-500",
};

export default function EventsPage() {
  const { t } = useTranslation("events");
  const { events, connected, missedCount, clearMissed } = useWebSocket();
  const [filterType, setFilterType] = useState("");
  const [filterRoom, setFilterRoom] = useState("");

  const filtered = events.filter((e: HubEvent) => {
    if (filterType && !e.type.includes(filterType)) return false;
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
            className={`h-2 w-2 rounded-full ${connected ? "bg-green-500 animate-pulse-slow" : "bg-[color:var(--text-faint)]"}`}
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
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        )}
      </div>

      {missedCount > 0 && (
        <div className="flex items-center justify-between card rounded-2xl px-4 py-3 border-primary-500/30 bg-primary-500/5">
          <div className="flex items-center gap-2">
            <Radio size={14} className="text-primary-400" />
            <span className="text-sm text-primary-300">{t("missed", { count: missedCount })}</span>
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
          {filtered.map((event: HubEvent) => {
            const isSignificant = SIGNIFICANT_TYPES.has(event.type);
            return (
              <div
                key={event.id}
                className={`card rounded-xl px-4 py-3 transition-all ${
                  isSignificant
                    ? `border-l-[3px] ${TYPE_COLORS[event.type] ?? "border-l-warm-500"}`
                    : ""
                }`}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold ${tierBgClass(event.tier)}`}
                  >
                    T{event.tier}
                  </span>
                  {event.room && (
                    <span className="text-xs text-[color:var(--text-muted)] font-medium">
                      {event.room}
                    </span>
                  )}
                  <span className="text-sm font-medium text-[color:var(--text)]">{event.type}</span>
                  <span className="ml-auto text-xs text-[color:var(--text-faint)]">
                    {shortDateTime(event.timestamp)}
                  </span>
                </div>
                {event.model_version && (
                  <p className="text-xs text-[color:var(--text-faint)] mt-1 font-mono">
                    {event.model_version}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
