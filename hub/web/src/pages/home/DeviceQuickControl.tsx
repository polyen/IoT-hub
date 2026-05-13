import { useState } from "react";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import type { DevicePlacement } from "../../lib/types";

interface Props {
  placement: DevicePlacement;
}

const KIND_ICON: Record<string, string> = {
  camera: "⬛", light: "💡", lock: "🔒", thermostat: "🌡", relay: "⚡",
  sensor_pir: "👁", sensor_door: "🚪", sensor_dht: "🌡", sensor_mq2: "💨",
  sensor_power: "⚡", speaker: "🔊",
};

export function DeviceQuickControl({ placement }: Props) {
  const [loading, setLoading] = useState(false);

  const sendCmd = async (payload: Record<string, unknown>) => {
    setLoading(true);
    try {
      await api.post(`/api/devices/${placement.device_id}/command`, payload);
    } finally {
      setLoading(false);
    }
  };

  const icon = KIND_ICON[placement.kind] ?? "⚙";
  const label = placement.label ?? placement.device_id;

  return (
    <div className="flex items-center justify-between py-2.5 border-b border-slate-700 light:border-slate-200 last:border-0">
      <div className="flex items-center gap-2">
        <span className="text-lg">{icon}</span>
        <div>
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-slate-500">{placement.kind}</p>
        </div>
      </div>
      <div className="flex gap-2">
        {placement.kind === "light" && (
          <>
            <Button size="sm" onClick={() => sendCmd({ state: "on" })} disabled={loading}>Вкл</Button>
            <Button size="sm" variant="ghost" onClick={() => sendCmd({ state: "off" })} disabled={loading}>Викл</Button>
          </>
        )}
        {(placement.kind === "relay") && (
          <>
            <Button size="sm" onClick={() => sendCmd({ cmd: "relay_on" })} disabled={loading}>Вкл</Button>
            <Button size="sm" variant="ghost" onClick={() => sendCmd({ cmd: "relay_off" })} disabled={loading}>Викл</Button>
          </>
        )}
        {placement.kind === "lock" && (
          <Button size="sm" variant="danger" onClick={() => sendCmd({ action: "unlock" })} disabled={loading}>
            Відкрити
          </Button>
        )}
        {!["light", "relay", "lock"].includes(placement.kind) && (
          <span className="text-xs text-slate-500">тільки читання</span>
        )}
      </div>
    </div>
  );
}
