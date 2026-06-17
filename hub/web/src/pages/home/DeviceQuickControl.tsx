import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { api, ApiError } from "../../lib/api";
import { Button } from "../../components/Button";
import { RangeSlider } from "../../components/RangeSlider";
import { deviceMeta } from "../../lib/deviceIcons";
import type { DevicePlacement } from "../../lib/types";

interface Props {
  placement: DevicePlacement;
  onDelete?: () => void;
}

interface CommandResult {
  result: string; // "auto_executed" | "confirm_required"
  confirm_id?: string;
}

type RefusalDetail = {
  failure_kind?: string;
  message?: string;
  cta?: { label: string; to: string } | null;
};

// Brightness tracks the themeable accent (was hard-coded amber #d97706);
// temperature stays on the fixed cool→warm→hot scale (semantic, theme-independent).
const BRIGHTNESS_GRADIENT = "linear-gradient(90deg, var(--primary-dim), var(--primary))";
const TEMP_GRADIENT = "linear-gradient(90deg, #38bdf8, #f59e0b, #ef4444)";

export function DeviceQuickControl({ placement, onDelete }: Props) {
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  // Initialise brightness from placement config or fall back to 80
  const [brightness, setBrightness] = useState<number>(
    typeof placement.config?.brightness === "number"
      ? (placement.config.brightness as number)
      : 80,
  );

  // Initialise target temperature from placement config or fall back to 22
  const [targetTemp, setTargetTemp] = useState<number>(
    typeof placement.config?.target_temp === "number"
      ? (placement.config.target_temp as number)
      : 22,
  );

  // Backend expects { payload, intent_text } (see routes/devices.py CommandBody),
  // and may gate the command behind policy → "confirm_required" or raise 403/404
  // with a structured detail {failure_kind, message, cta}.
  const sendCmd = async (payload: Record<string, unknown>) => {
    setLoading(true);
    try {
      const res = await api.post<CommandResult>(
        `/api/devices/${placement.device_id}/command`,
        { payload, intent_text: "UI command" },
        /* silent */ true,
      );
      if (res.result === "confirm_required") toast.info("Потрібне підтвердження");
      else toast.success("Виконано");
    } catch (e) {
      if (e instanceof ApiError && e.detail !== null && typeof e.detail === "object") {
        const refusal = e.detail as RefusalDetail;
        if (typeof refusal.message === "string") {
          const cta = refusal.cta;
          toast.error(
            refusal.message,
            cta
              ? { action: { label: cta.label, onClick: () => navigate(cta.to) } }
              : undefined,
          );
          return;
        }
      }
      toast.error("Не вдалося виконати команду");
    } finally {
      setLoading(false);
    }
  };

  const handleBrightnessCommit = (value: number) => {
    setBrightness(value);
    sendCmd({ state: "on", brightness: value });
  };

  const handleTempCommit = (value: number) => {
    setTargetTemp(value);
    sendCmd({ target_temp: value });
  };

  const meta = deviceMeta(placement.kind);
  const { Icon } = meta;
  const label = placement.label ?? placement.device_id;

  const isLight = placement.kind === "light";
  const isThermostat = placement.kind === "thermostat";
  const isRelay = placement.kind === "relay";
  const isLock = placement.kind === "lock";
  const isReadOnly = !isLight && !isThermostat && !isRelay && !isLock;

  return (
    <div className="py-2.5 border-b border-[color:var(--border)] last:border-0">
      {/* ── Top row: icon + label + action buttons ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${meta.bg}`}>
            <Icon size={16} strokeWidth={1.9} className={meta.text} />
          </span>
          <div>
            <p className="text-sm font-medium">{label}</p>
            <p className="text-xs text-[color:var(--text-faint)]">{meta.label}</p>
          </div>
        </div>
        <div className="flex gap-2 items-center">
          {isLight && (
            <>
              <Button size="md" onClick={() => sendCmd({ state: "on" })} disabled={loading}>Вкл</Button>
              <Button size="md" variant="ghost" onClick={() => sendCmd({ state: "off" })} disabled={loading}>Викл</Button>
            </>
          )}
          {isRelay && (
            <>
              <Button size="md" onClick={() => sendCmd({ cmd: "relay_on" })} disabled={loading}>Вкл</Button>
              <Button size="md" variant="ghost" onClick={() => sendCmd({ cmd: "relay_off" })} disabled={loading}>Викл</Button>
            </>
          )}
          {isLock && (
            <Button size="md" variant="danger" onClick={() => sendCmd({ action: "unlock" })} disabled={loading}>
              Відкрити
            </Button>
          )}
          {isReadOnly && (
            <span className="text-xs text-[color:var(--text-faint)]">тільки читання</span>
          )}
          {onDelete && (
            <button
              onClick={onDelete}
              title="Прибрати з кімнати"
              className="ml-1 p-2 min-h-[36px] min-w-[36px] rounded-lg flex items-center justify-center text-[color:var(--text-faint)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
            >
              <Trash2 size={15} />
            </button>
          )}
        </div>
      </div>

      {/* ── Brightness slider (light only) ── */}
      {isLight && (
        <div className="mt-2 pl-[42px] pr-1">
          <RangeSlider
            value={brightness}
            min={0}
            max={100}
            step={1}
            onCommit={handleBrightnessCommit}
            label={`Яскравість: ${label}`}
            valueLabel={(v) => `${v}%`}
            gradient={BRIGHTNESS_GRADIENT}
            disabled={loading}
          />
        </div>
      )}

      {/* ── Temperature slider (thermostat only) ── */}
      {isThermostat && (
        <div className="mt-2 pl-[42px] pr-1">
          <RangeSlider
            value={targetTemp}
            min={16}
            max={30}
            step={0.5}
            onCommit={handleTempCommit}
            label={`Температура: ${label}`}
            valueLabel={(v) => `${v}°`}
            gradient={TEMP_GRADIENT}
            disabled={loading}
          />
        </div>
      )}
    </div>
  );
}
