import { useState } from "react";
import { Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { deviceMeta } from "../../lib/deviceIcons";
import type { DevicePlacement } from "../../lib/types";

interface Props {
  placement: DevicePlacement;
  onDelete?: () => void;
}

export function DeviceQuickControl({ placement, onDelete }: Props) {
  const [loading, setLoading] = useState(false);

  const sendCmd = async (payload: Record<string, unknown>) => {
    setLoading(true);
    try {
      await api.post(`/api/devices/${placement.device_id}/command`, payload);
    } finally {
      setLoading(false);
    }
  };

  const meta = deviceMeta(placement.kind);
  const { Icon } = meta;
  const label = placement.label ?? placement.device_id;

  return (
    <div className="flex items-center justify-between py-2.5 border-b border-[color:var(--border)] last:border-0">
      <div className="flex items-center gap-2.5">
        <span className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${meta.bg}`}>
          <Icon size={16} strokeWidth={1.9} className={meta.text} />
        </span>
        <div>
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-[color:var(--text-faint)]">{meta.label}</p>
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
          <span className="text-xs text-[color:var(--text-faint)]">тільки читання</span>
        )}
        {onDelete && (
          <button
            onClick={onDelete}
            title="Прибрати з кімнати"
            className="ml-1 p-1.5 rounded-lg text-[color:var(--text-faint)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
          >
            <Trash2 size={15} />
          </button>
        )}
      </div>
    </div>
  );
}
