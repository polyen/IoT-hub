/**
 * AmbiguityResolver — renders candidate device buttons when the agent returns an
 * AMBIGUOUS failure. The user taps a device; we POST to /api/agent/disambiguate
 * which re-runs the intent with a forced device_id.
 */
import { useState } from "react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import type { DisambiguateCandidate } from "../../lib/types";

const KIND_ICON: Record<string, string> = {
  light: "💡",
  relay: "🔌",
  thermostat: "🌡️",
  lock: "🔒",
  speaker: "🔊",
  camera: "📷",
  sensor_pir: "👁️",
  sensor_door: "🚪",
  sensor_dht: "🌡️",
  sensor_mq2: "💨",
  sensor_power: "⚡",
};

interface AmbiguityResolverProps {
  intentText: string;
  candidates: DisambiguateCandidate[];
  onResolved?: () => void;
}

export function AmbiguityResolver({ intentText, candidates, onResolved }: AmbiguityResolverProps) {
  const [loading, setLoading] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);

  async function choose(deviceId: string) {
    if (loading || resolved) return;
    setLoading(deviceId);
    try {
      await api.post("/api/agent/disambiguate", {
        intent_text: intentText,
        chosen_device_id: deviceId,
      });
      toast.success("Команду уточнено — виконується…");
      setResolved(true);
      onResolved?.();
    } catch {
      toast.error("Не вдалося обрати пристрій");
    } finally {
      setLoading(null);
    }
  }

  if (resolved) {
    return (
      <p className="mt-2 text-[10px] text-green-400/80">✅ Команду уточнено</p>
    );
  }

  return (
    <div className="mt-2 space-y-1">
      <p className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">
        Оберіть пристрій:
      </p>
      {candidates.map((c) => (
        <button
          key={c.device_id}
          onClick={() => choose(c.device_id)}
          disabled={loading !== null}
          className={[
            "w-full flex items-center justify-between rounded px-2.5 py-1.5 text-xs text-left transition-colors",
            "border border-slate-700 bg-slate-800/60 hover:bg-slate-700/60 hover:border-slate-600",
            loading === c.device_id ? "opacity-50 cursor-wait" : "cursor-pointer",
          ].join(" ")}
        >
          <span className="flex items-center gap-1.5 text-white/80">
            <span>{KIND_ICON[c.kind] ?? "📦"}</span>
            <span>{c.label ?? c.device_id}</span>
          </span>
          <span className="text-slate-500 text-[10px]">{c.room}</span>
        </button>
      ))}
    </div>
  );
}
