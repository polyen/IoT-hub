import { useState } from "react";
import { useWebSocket } from "../../hooks/useWebSocket";
import { useTranslation } from "react-i18next";
import { tierBgClass } from "../../lib/tier";
import { shortDateTime } from "../../lib/format";
import { EmptyState } from "../../components/EmptyState";
import type { HubEvent } from "../../lib/types";

const SIGNIFICANT_TYPES = new Set(["alert", "camera/event", "stranger", "fall", "gas"]);

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
    <div>
      <div className="flex items-center justify-between mb-3">
        <h1 className="text-xl font-semibold">{t("title")}</h1>
        <span
          className={`text-xs px-2 py-1 rounded-full ${
            connected ? "bg-green-900 text-green-300" : "bg-slate-800 text-slate-500"
          }`}
        >
          {connected ? "● live" : "○ ..."}
        </span>
      </div>

      {/* Filters */}
      <div className="flex gap-2 mb-3 flex-wrap">
        <input
          type="text"
          placeholder={t("filter.placeholder")}
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="flex-1 min-w-0 text-sm px-3 py-1.5 rounded-lg bg-slate-800 light:bg-white border border-slate-700 light:border-slate-300 focus:outline-none focus:border-blue-500"
        />
        <select
          value={filterRoom}
          onChange={(e) => setFilterRoom(e.target.value)}
          className="text-sm px-2 py-1.5 rounded-lg bg-slate-800 light:bg-white border border-slate-700 light:border-slate-300 focus:outline-none"
        >
          <option value="">{t("filter.all")}</option>
          {rooms.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
      </div>

      {missedCount > 0 && (
        <div className="flex items-center justify-between bg-blue-950 border border-blue-800 rounded-lg px-4 py-2 mb-3 text-sm">
          <span className="text-blue-300">{t("missed", { count: missedCount })}</span>
          <button onClick={clearMissed} className="text-blue-400 hover:text-white ml-3">
            {t("missed_close")}
          </button>
        </div>
      )}

      {filtered.length === 0 ? (
        <EmptyState message={t("title") + " — немає"} />
      ) : (
        <div className="space-y-2">
          {filtered.map((event: HubEvent) => (
            <div
              key={event.id}
              className={`rounded-lg border bg-slate-800 light:bg-white p-3 ${
                SIGNIFICANT_TYPES.has(event.type)
                  ? "border-l-4 border-l-amber-500 border-slate-700"
                  : "border-slate-700 light:border-slate-200"
              }`}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`text-xs px-1.5 py-0.5 rounded border font-mono ${tierBgClass(event.tier)}`}
                >
                  T{event.tier}
                </span>
                {event.room && (
                  <span className="text-slate-400 text-xs">{event.room}</span>
                )}
                <span className="text-sm font-medium">{event.type}</span>
                <span className="ml-auto text-xs text-slate-500">{shortDateTime(event.timestamp)}</span>
              </div>
              {event.model_version && (
                <p className="text-xs text-slate-600 mt-1">model: {event.model_version}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
