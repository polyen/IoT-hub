import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Spinner } from "../../components/Spinner";
import type { DigestData } from "../../lib/types";

type Period = "today" | "yesterday" | "week";

const PERIOD_LABELS: Record<Period, string> = {
  today: "Сьогодні",
  yesterday: "Вчора",
  week: "Тиждень",
};

const EVENT_LABELS: Record<string, string> = {
  person_detected: "Людина",
  motion: "Рух",
  door_open: "Двері",
  fire: "Вогонь",
  smoke: "Дим",
  face_recognized: "Обличчя",
  fall_detected: "Падіння",
  unknown: "Інше",
};

const HOURS = Array.from({ length: 24 }, (_, i) => i);

function PeakHourBadge({ hour }: { hour: number }) {
  const label = hour < 12
    ? `${hour}:00 – ${hour + 1}:00`
    : `${hour}:00 – ${hour + 1}:00`;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-blue-900/50 px-2.5 py-0.5 text-xs text-blue-300">
      🕐 Пік: {label}
    </span>
  );
}

function EventBar({ label, count, max }: { label: string; count: number; max: number }) {
  const pct = max > 0 ? (count / max) * 100 : 0;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-32 shrink-0 text-slate-300 truncate">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-slate-700">
        <div className="h-2 rounded-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right text-slate-400 text-xs">{count}</span>
    </div>
  );
}

export default function DigestPage() {
  const [period, setPeriod] = useState<Period>("today");

  const { data, isLoading, isFetching } = useQuery<DigestData>({
    queryKey: ["digest", period],
    queryFn: () => api.get<DigestData>(`/api/digest?period=${period}`),
    staleTime: 60_000,
  });

  const maxCount = data ? Math.max(1, ...Object.values(data.counts)) : 1;

  const sorted = data
    ? Object.entries(data.counts).sort(([, a], [, b]) => b - a)
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Дайджест</h1>
        {isFetching && <Spinner className="h-4 w-4" />}
      </div>

      {/* Period selector */}
      <div className="flex gap-1 rounded-lg bg-slate-800/60 p-1 border border-slate-700">
        {(["today", "yesterday", "week"] as Period[]).map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={`flex-1 rounded-md py-1.5 text-sm font-medium transition-colors ${
              period === p
                ? "bg-blue-600 text-white"
                : "text-slate-400 hover:text-white"
            }`}
          >
            {PERIOD_LABELS[p]}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex justify-center pt-12"><Spinner className="h-8 w-8" /></div>
      ) : !data ? (
        <div className="py-12 text-center text-slate-400">Немає даних</div>
      ) : (
        <>
          {/* Summary row */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div className="rounded-lg border border-slate-700 bg-slate-800/60 p-4 text-center">
              <p className="text-3xl font-bold">{data.total_events}</p>
              <p className="text-xs text-slate-400 mt-1">подій всього</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800/60 p-4 text-center">
              <p className="text-3xl font-bold">{Object.keys(data.counts).length}</p>
              <p className="text-xs text-slate-400 mt-1">типів</p>
            </div>
            {data.peak_hour != null && (
              <div className="col-span-2 sm:col-span-1 rounded-lg border border-slate-700 bg-slate-800/60 p-4 flex items-center justify-center">
                <PeakHourBadge hour={data.peak_hour} />
              </div>
            )}
          </div>

          {/* Narrative */}
          {data.narrative ? (
            <section className="rounded-lg border border-blue-900 bg-blue-950/40 p-4 text-sm text-slate-200 leading-relaxed space-y-1">
              <p className="text-xs text-blue-400 font-semibold uppercase tracking-wider mb-2">AI-резюме</p>
              <p>{data.narrative}</p>
            </section>
          ) : (
            <div className="rounded-lg border border-slate-700 bg-slate-800/40 px-4 py-3 text-xs text-slate-500">
              Резюме ще генерується або агент недоступний
            </div>
          )}

          {/* Event breakdown */}
          {sorted.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Події за типом</h2>
              <div className="rounded-lg border border-slate-700 bg-slate-800/60 p-4 space-y-3">
                {sorted.map(([type, count]) => (
                  <EventBar
                    key={type}
                    label={EVENT_LABELS[type] ?? type}
                    count={count}
                    max={maxCount}
                  />
                ))}
              </div>
            </section>
          )}

          {/* 24h activity strip */}
          <section className="space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Активність по годинах</h2>
            <div className="rounded-lg border border-slate-700 bg-slate-800/60 p-4">
              <p className="text-xs text-slate-500 text-center">
                {data.peak_hour != null
                  ? `Найактивніша година: ${data.peak_hour}:00–${data.peak_hour + 1}:00`
                  : "Немає даних для побудови графіку"}
              </p>
              <div className="mt-3 flex items-end gap-px h-10">
                {HOURS.map((h) => {
                  const isActive = h === data.peak_hour;
                  return (
                    <div
                      key={h}
                      title={`${h}:00`}
                      className={`flex-1 rounded-sm ${isActive ? "bg-blue-500" : "bg-slate-700"}`}
                      style={{ height: isActive ? "100%" : "30%" }}
                    />
                  );
                })}
              </div>
              <div className="flex justify-between mt-1 text-xs text-slate-600">
                <span>00:00</span><span>12:00</span><span>23:00</span>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
