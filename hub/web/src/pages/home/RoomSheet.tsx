import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, X } from "lucide-react";
import { Sheet } from "../../components/Dialog";
import { Button } from "../../components/Button";
import { DeviceQuickControl } from "./DeviceQuickControl";
import { DeviceIcon, deviceMeta, DEVICE_KINDS } from "../../lib/deviceIcons";
import { api } from "../../lib/api";
import type { Room, DeviceKind, DevicePlacement, FloorPlanData } from "../../lib/types";

interface DiscoveredDevice {
  device_id: string;
  kind_guess: DeviceKind;
  last_seen: string | null;
  source: "mqtt" | "redis";
}

interface Props {
  room: Room | null;
  data: FloorPlanData;
  onClose: () => void;
}

const ROOM_TYPE_LABEL: Record<string, string> = {
  bedroom: "Спальня", kitchen: "Кухня", living: "Вітальня",
  bath: "Ванна", hall: "Коридор", outdoor: "Надвір", other: "Інше",
};

function uuid4(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) => {
    const n = parseInt(c, 10);
    return (n ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (n / 4)))).toString(16);
  });
}

export function RoomSheet({ room, data, onClose }: Props) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<DeviceKind>("light");
  const [label, setLabel] = useState("");
  const [deviceId, setDeviceId] = useState("");

  const { data: discovered } = useQuery<DiscoveredDevice[]>({
    queryKey: ["discovered-devices"],
    queryFn: () => api.get<DiscoveredDevice[]>("/api/floorplan/devices/discovered"),
    enabled: adding,
    staleTime: 30_000,
  });

  const save = useMutation({
    mutationFn: (next: FloorPlanData) => api.put<FloorPlanData>("/api/floorplan", next, false, 30_000),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["floorplan"] });
      qc.invalidateQueries({ queryKey: ["room_states"] });
    },
    onError: () => toast.error("Не вдалося зберегти"),
  });

  if (!room) return null;
  const roomPlacements = data.placements.filter((p) => p.room_id === room.id);

  const cx = room.polygon.reduce((s, [x]) => s + x, 0) / room.polygon.length;
  const cy = room.polygon.reduce((s, [, y]) => s + y, 0) / room.polygon.length;

  function resetForm() {
    setAdding(false);
    setKind("light");
    setLabel("");
    setDeviceId("");
  }

  function addDevice() {
    if (!room) return;
    const placement: DevicePlacement = {
      id: uuid4(),
      room_id: room.id,
      device_id: deviceId.trim() || `${kind}_${Date.now()}`,
      kind,
      x: cx,
      y: cy,
      label: label.trim() || null,
      config: {},
      aliases: [],
      controllable: ["light", "relay", "lock", "thermostat"].includes(kind),
      actions: [],
    };
    save.mutate(
      { ...data, placements: [...data.placements, placement] },
      { onSuccess: () => { toast.success("Пристрій додано"); resetForm(); } },
    );
  }

  function deleteDevice(id: string) {
    save.mutate(
      { ...data, placements: data.placements.filter((p) => p.id !== id) },
      { onSuccess: () => toast.success("Прибрано з кімнати") },
    );
  }

  return (
    <Sheet glass open={!!room} onOpenChange={(o) => !o && onClose()} title={room.name}>
      <p className="text-xs text-[color:var(--text-muted)] -mt-1 mb-3">
        {ROOM_TYPE_LABEL[room.type] ?? room.type} · {roomPlacements.length} пристроїв
      </p>

      {roomPlacements.length === 0 ? (
        <p className="text-sm text-[color:var(--text-muted)] py-4 text-center">
          Немає пристроїв у цій кімнаті
        </p>
      ) : (
        <div>
          {roomPlacements.map((p) => (
            <DeviceQuickControl key={p.id} placement={p} onDelete={() => deleteDevice(p.id)} />
          ))}
        </div>
      )}

      {/* ── Add device ── */}
      {!adding ? (
        <Button
          variant="secondary"
          size="sm"
          className="mt-3 w-full gap-1.5"
          onClick={() => setAdding(true)}
        >
          <Plus size={15} /> Додати пристрій
        </Button>
      ) : (
        <div className="mt-3 rounded-xl border border-[color:var(--border)] bg-[color:var(--raised)] p-3 space-y-3 animate-fade-in">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold text-[color:var(--text)]">Новий пристрій</p>
            <button onClick={resetForm} className="text-[color:var(--text-faint)] hover:text-[color:var(--text)]">
              <X size={15} />
            </button>
          </div>

          {/* Kind grid */}
          <div className="grid grid-cols-4 gap-1">
            {DEVICE_KINDS.map((k) => (
              <button
                key={k}
                onClick={() => setKind(k)}
                className={[
                  "flex flex-col items-center gap-1 rounded-lg py-2 px-1 text-center transition-all border",
                  kind === k
                    ? "bg-primary-600/20 text-primary-300 border-primary-500/40"
                    : "text-[color:var(--text-muted)] hover:bg-[color:var(--card)] border-transparent",
                ].join(" ")}
              >
                <DeviceIcon kind={k} size={17} className={deviceMeta(k).text} />
                <span className="text-xs leading-tight">{deviceMeta(k).label}</span>
              </button>
            ))}
          </div>

          {/* Discovered MQTT devices */}
          {discovered && discovered.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs font-mono uppercase tracking-wider text-[color:var(--text-faint)]">
                Виявлені (MQTT)
              </p>
              <div className="flex flex-wrap gap-1.5">
                {discovered.map((d) => (
                  <button
                    key={d.device_id}
                    onClick={() => { setKind(d.kind_guess); setDeviceId(d.device_id); }}
                    className={[
                      "flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs border transition-all",
                      deviceId === d.device_id
                        ? "bg-primary-600/20 text-primary-300 border-primary-500/40"
                        : "text-[color:var(--text-muted)] hover:bg-[color:var(--card)] border-[color:var(--border)]",
                    ].join(" ")}
                  >
                    <DeviceIcon kind={d.kind_guess} size={13} className={deviceMeta(d.kind_guess).text} />
                    <span className="font-mono max-w-[110px] truncate">{d.device_id}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Назва (напр. Лампа над столом)"
            className="w-full rounded-lg border border-[color:var(--border)] bg-[color:var(--card)] px-3 py-2 text-sm text-[color:var(--text)] placeholder-[color:var(--text-faint)] focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <input
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            placeholder="device_id (необов'язково)"
            className="w-full rounded-lg border border-[color:var(--border)] bg-[color:var(--card)] px-3 py-2 text-sm font-mono text-[color:var(--text)] placeholder-[color:var(--text-faint)] focus:outline-none focus:ring-2 focus:ring-primary-500"
          />

          <Button
            variant="primary"
            size="sm"
            className="w-full"
            disabled={save.isPending}
            onClick={addDevice}
          >
            {save.isPending ? "Додаємо…" : "Додати в кімнату"}
          </Button>
        </div>
      )}
    </Sheet>
  );
}
