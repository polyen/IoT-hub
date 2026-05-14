import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Spinner } from "../../components/Spinner";
import { relativeTime } from "../../lib/format";
import type { Incident, SystemHealth } from "../../lib/types";

const STATUS_DOT: Record<string, string> = {
  ok: "bg-green-500",
  warn: "bg-amber-500",
  error: "bg-red-500",
  offline: "bg-slate-600",
};

const SEVERITY_STYLE: Record<string, string> = {
  high: "border-red-800 bg-red-900/30 text-red-300",
  medium: "border-amber-800 bg-amber-900/30 text-amber-300",
  low: "border-slate-700 bg-slate-800/60 text-slate-400",
};

function GaugeBar({ value, max, warn, danger }: { value: number; max: number; warn?: number; danger?: number }) {
  const pct = Math.min(100, (value / max) * 100);
  const color =
    danger && value >= danger ? "bg-red-500" :
    warn && value >= warn ? "bg-amber-500" :
    "bg-blue-500";
  return (
    <div className="h-2 w-full rounded-full bg-slate-700">
      <div className={`h-2 rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function LogModal({ service, onClose }: { service: string; onClose: () => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function fetchLogs() {
    try {
      const data = await api.get<string[]>(`/api/system/logs/${service}?tail=200`);
      setLines(data);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchLogs();
    const id = setInterval(fetchLogs, 2000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [service]);

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
          <span className="font-semibold text-sm">Логи: {service}</span>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-lg leading-none">×</button>
        </div>
        {loading ? (
          <div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div>
        ) : (
          <div className="overflow-auto flex-1 p-4 font-mono text-xs text-slate-300 space-y-0.5 leading-5">
            {lines.map((line, i) => (
              <div key={i} className={line.includes("ERROR") || line.includes("CRITICAL") ? "text-red-400" : line.includes("WARN") ? "text-amber-400" : ""}>
                {line}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
        <div className="px-4 py-2 border-t border-slate-700 text-xs text-slate-500">
          Оновлення кожні 2 с
        </div>
      </div>
    </div>
  );
}

export default function SystemPage() {
  const [logService, setLogService] = useState<string | null>(null);

  // REST fallback — initial load
  const { data: restData, isLoading } = useQuery<SystemHealth>({
    queryKey: ["system-health"],
    queryFn: () => api.get<SystemHealth>("/api/system/health"),
    refetchInterval: 30_000, // WS is primary; REST only as fallback
  });

  const { data: incidents } = useQuery<Incident[]>({
    queryKey: ["system-incidents"],
    queryFn: () => api.get<Incident[]>("/api/system/incidents"),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  // Live WS — replaces REST poll while connected
  const [liveData, setLiveData] = useState<SystemHealth | null>(null);
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/api/system/ws`);
    ws.onmessage = (e) => {
      try { setLiveData(JSON.parse(e.data)); } catch { /* ignore */ }
    };
    return () => ws.close();
  }, []);

  const data = liveData ?? restData;

  if (isLoading && !data) return <div className="flex justify-center pt-16"><Spinner className="h-8 w-8" /></div>;
  if (!data) return <div className="py-16 text-center text-slate-400">Не вдалося завантажити стан системи</div>;

  const ramPct = data.hardware.ram_total_gb > 0
    ? (data.hardware.ram_used_gb / data.hardware.ram_total_gb) * 100
    : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Стан системи</h1>
        {liveData && <span className="flex items-center gap-1.5 text-xs text-green-400"><span className="h-1.5 w-1.5 rounded-full bg-green-400 animate-pulse" />live</span>}
      </div>

      {/* Services */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Сервіси</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {data.services.map((svc) => (
            <div key={svc.name} className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2.5">
              <span className={`h-2.5 w-2.5 rounded-full shrink-0 ${STATUS_DOT[svc.status] ?? "bg-slate-600"}`} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium capitalize">{svc.name}</p>
                {svc.uptime && <p className="text-xs text-slate-500 truncate">{relativeTime(svc.uptime)}</p>}
              </div>
              <button
                onClick={() => setLogService(svc.name)}
                className="shrink-0 text-xs text-slate-500 hover:text-slate-300 px-1.5 py-0.5 rounded hover:bg-slate-700"
              >
                логи
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* Hardware */}
      <section className="space-y-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Обладнання</h2>
        <div className="rounded-lg border border-slate-700 bg-slate-800/60 p-4 space-y-4">
          <div className="space-y-1.5">
            <div className="flex justify-between text-sm">
              <span>CPU</span>
              <span className="text-slate-400">{data.hardware.cpu_pct.toFixed(1)}%</span>
            </div>
            <GaugeBar value={data.hardware.cpu_pct} max={100} warn={70} danger={90} />
          </div>
          <div className="space-y-1.5">
            <div className="flex justify-between text-sm">
              <span>RAM</span>
              <span className="text-slate-400">
                {data.hardware.ram_used_gb.toFixed(1)} / {data.hardware.ram_total_gb.toFixed(1)} GB
              </span>
            </div>
            <GaugeBar value={ramPct} max={100} warn={75} danger={90} />
          </div>
          {data.hardware.npu_pct != null && (
            <div className="space-y-1.5">
              <div className="flex justify-between text-sm">
                <span>NPU (Hailo-8)</span>
                <span className="text-slate-400">{data.hardware.npu_pct.toFixed(1)}%</span>
              </div>
              <GaugeBar value={data.hardware.npu_pct} max={100} warn={80} danger={95} />
            </div>
          )}
          <div className="grid grid-cols-2 gap-4 pt-1 text-sm">
            <div>
              <p className="text-slate-400 text-xs">NVMe вільно</p>
              <p className="font-medium">{data.hardware.nvme_free_gb.toFixed(1)} GB</p>
            </div>
            {data.hardware.temp_c != null && (
              <div>
                <p className="text-slate-400 text-xs">Температура</p>
                <p className={`font-medium ${data.hardware.temp_c >= 80 ? "text-red-400" : data.hardware.temp_c >= 65 ? "text-amber-400" : ""}`}>
                  {data.hardware.temp_c.toFixed(1)} °C
                </p>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* Latency */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Затримки</h2>
        <div className="grid grid-cols-3 gap-3">
          {([
            { label: "CV p50", value: data.latency.cv_p50_ms },
            { label: "CV p95", value: data.latency.cv_p95_ms },
            { label: "Voice e2e p50", value: data.latency.voice_e2e_p50_ms },
          ] as const).map(({ label, value }) => (
            <div key={label} className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-3 text-center">
              <p className="text-xl font-bold">
                {value != null ? value : <span className="text-slate-600">—</span>}
              </p>
              {value != null && <p className="text-xs text-slate-500">мс</p>}
              <p className="text-xs text-slate-500 mt-1">{label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Models */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Моделі</h2>
        <div className="rounded-lg border border-slate-700 bg-slate-800/60 divide-y divide-slate-700">
          {([
            { label: "CV (YOLO26n)", value: data.models.cv_version },
            { label: "LLM (Qwen)", value: data.models.llm_version },
            { label: "ASR (Whisper)", value: data.models.whisper_version },
          ] as const).map(({ label, value }) => (
            <div key={label} className="flex items-center justify-between px-4 py-2.5 text-sm">
              <span className="text-slate-300">{label}</span>
              <span className="font-mono text-xs text-slate-400">{value ?? "—"}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Sync */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Bridge sync</h2>
        <div className="rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm flex items-center justify-between">
          <div>
            <p className="text-slate-400 text-xs">Останній sync</p>
            <p>{data.sync.last_bridge_ts ? relativeTime(data.sync.last_bridge_ts) : "Ніколи"}</p>
          </div>
          <div className="text-right">
            <p className="text-slate-400 text-xs">T1 черга</p>
            <p className={data.sync.t1_queue_depth > 100 ? "text-amber-400" : ""}>{data.sync.t1_queue_depth}</p>
          </div>
        </div>
      </section>

      {/* Incidents */}
      {incidents && incidents.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Інциденти (7 днів)
          </h2>
          <div className="space-y-1.5">
            {incidents.map((inc) => (
              <div key={inc.id} className={`rounded-lg border px-4 py-2.5 text-sm ${SEVERITY_STYLE[inc.severity]}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium truncate">{inc.intent_text}</span>
                  <span className="text-xs shrink-0 opacity-60">{new Date(inc.timestamp).toLocaleString("uk-UA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                </div>
                {inc.tool && <p className="text-xs opacity-60 mt-0.5 font-mono">{inc.tool}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      {logService && <LogModal service={logService} onClose={() => setLogService(null)} />}
    </div>
  );
}
