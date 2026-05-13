import { useState } from "react";
import { useForm } from "react-hook-form";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import type { DeviceKind, DevicePlacement, FloorPlanData, Room } from "../../lib/types";

const KIND_ICON: Record<string, string> = {
  camera: "📷", light: "💡", lock: "🔒", thermostat: "🌡", relay: "⚡",
  sensor_pir: "👁", sensor_door: "🚪", sensor_dht: "💧", sensor_mq2: "💨",
  sensor_power: "🔌", speaker: "🔊",
};

const KIND_LABELS: Record<string, string> = {
  camera: "Камера", light: "Лампа", lock: "Замок", thermostat: "Термостат",
  relay: "Реле", sensor_pir: "PIR", sensor_door: "Двері",
  sensor_dht: "Темп/Вологість", sensor_mq2: "Газ MQ-2",
  sensor_power: "Лічильник", speaker: "Динамік",
};

const DEVICE_KINDS: DeviceKind[] = [
  "camera", "light", "lock", "thermostat", "relay",
  "sensor_pir", "sensor_door", "sensor_dht", "sensor_mq2", "sensor_power", "speaker",
];

interface EditForm {
  label: string;
  device_id: string;
  kind: DeviceKind;
  mqtt_topic: string;
}

interface DeviceCardProps {
  placement: DevicePlacement;
  room: Room | undefined;
  onSave: (updated: DevicePlacement) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function DeviceCard({ placement, room, onSave, onDelete }: DeviceCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [saving, setSaving] = useState(false);

  const form = useForm<EditForm>({
    defaultValues: {
      label: placement.label ?? "",
      device_id: placement.device_id,
      kind: placement.kind as DeviceKind,
      mqtt_topic: (placement.config.mqtt_topic as string) ?? "",
    },
  });

  async function handleSave(data: EditForm) {
    setSaving(true);
    try {
      const config = { ...placement.config };
      if (data.mqtt_topic) config.mqtt_topic = data.mqtt_topic;
      else delete config.mqtt_topic;
      await onSave({
        ...placement,
        label: data.label || null,
        device_id: data.device_id,
        kind: data.kind,
        config,
      });
      setExpanded(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="text-2xl w-8 shrink-0 text-center" aria-hidden>
          {KIND_ICON[placement.kind] ?? "⚙"}
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-sm truncate">
            {placement.label || placement.device_id}
          </p>
          <p className="text-xs text-slate-500 truncate">
            {KIND_LABELS[placement.kind] ?? placement.kind}
            {room ? ` · ${room.name}` : ""}
            {" · "}<span className="font-mono">{placement.device_id}</span>
          </p>
        </div>
        <button
          onClick={() => setExpanded((v) => !v)}
          className="shrink-0 rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-700 hover:text-white"
        >
          {expanded ? "Закрити" : "Редагувати"}
        </button>
      </div>

      {expanded && (
        <form
          onSubmit={form.handleSubmit(handleSave)}
          className="border-t border-slate-700 bg-slate-900/40 px-4 py-3 space-y-3"
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="block text-xs text-slate-400">Назва (label)</span>
              <input
                {...form.register("label")}
                className="w-full rounded border border-slate-600 bg-slate-700 px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="напр. Лампа в коридорі"
              />
            </label>
            <label className="space-y-1">
              <span className="block text-xs text-slate-400">Device ID</span>
              <input
                {...form.register("device_id", { required: true })}
                className="w-full rounded border border-slate-600 bg-slate-700 px-3 py-1.5 text-sm text-white font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="light_hallway"
              />
            </label>
            <label className="space-y-1">
              <span className="block text-xs text-slate-400">Тип пристрою</span>
              <select
                {...form.register("kind")}
                className="w-full rounded border border-slate-600 bg-slate-700 px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                {DEVICE_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {KIND_ICON[k]} {KIND_LABELS[k] ?? k}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="block text-xs text-slate-400">
                MQTT topic{" "}
                <span className="text-slate-600">(порожньо = home/{"{id}"}/cmd)</span>
              </span>
              <input
                {...form.register("mqtt_topic")}
                className="w-full rounded border border-slate-600 bg-slate-700 px-3 py-1.5 text-sm text-white font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder={`home/${placement.device_id}/cmd`}
              />
            </label>
          </div>
          <div className="flex items-center gap-2 pt-1">
            <Button type="submit" size="sm" variant="primary" disabled={saving}>
              {saving ? "Зберігаємо…" : "Зберегти"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => { form.reset(); setExpanded(false); }}
            >
              Скасувати
            </Button>
            <button
              type="button"
              onClick={() => onDelete(placement.id)}
              className="ml-auto rounded px-2 py-1 text-xs text-red-400 hover:bg-red-900/30 hover:text-red-300"
            >
              Видалити
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

export default function DevicesListPage() {
  const { data, isLoading, error } = useFloorPlan();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");

  const saveMutation = useMutation({
    mutationFn: (updated: FloorPlanData) => api.put<FloorPlanData>("/api/floorplan", updated),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["floorplan"] });
      toast.success("Збережено");
    },
  });

  if (isLoading) {
    return <div className="flex justify-center pt-16"><Spinner className="h-8 w-8" /></div>;
  }
  if (error || !data) {
    return <EmptyState message="Не вдалося завантажити пристрої" icon="⚙" />;
  }

  const roomById = Object.fromEntries(data.rooms.map((r) => [r.id, r]));

  const filtered = data.placements.filter((p) => {
    const q = search.toLowerCase();
    return (
      !q ||
      p.device_id.toLowerCase().includes(q) ||
      (p.label ?? "").toLowerCase().includes(q) ||
      p.kind.toLowerCase().includes(q) ||
      (roomById[p.room_id]?.name ?? "").toLowerCase().includes(q)
    );
  });

  // Group by room_id preserving encounter order
  const byRoom = new Map<string, DevicePlacement[]>();
  for (const p of filtered) {
    const list = byRoom.get(p.room_id) ?? [];
    list.push(p);
    byRoom.set(p.room_id, list);
  }

  async function handleSave(updated: DevicePlacement) {
    const newData: FloorPlanData = {
      ...data!,
      placements: data!.placements.map((p) => (p.id === updated.id ? updated : p)),
    };
    await saveMutation.mutateAsync(newData);
  }

  async function handleDelete(id: string) {
    const newData: FloorPlanData = {
      ...data!,
      placements: data!.placements.filter((p) => p.id !== id),
    };
    await saveMutation.mutateAsync(newData);
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold flex-1">Пристрої</h1>
        <span className="text-xs text-slate-500">{data.placements.length} шт.</span>
      </div>

      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Пошук за назвою, типом або кімнатою…"
        className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
      />

      {filtered.length === 0 && (
        <EmptyState
          message={search ? "Нічого не знайдено" : "Пристроїв ще немає — додай їх у плані будинку."}
          icon="⚙"
        />
      )}

      {Array.from(byRoom.entries()).map(([roomId, placements]) => {
        const room = roomById[roomId];
        return (
          <section key={roomId} className="space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              {room?.name ?? "Невідома кімната"}
            </h2>
            {placements.map((p) => (
              <DeviceCard
                key={p.id}
                placement={p}
                room={room}
                onSave={handleSave}
                onDelete={handleDelete}
              />
            ))}
          </section>
        );
      })}

      <p className="pt-2 text-center text-xs text-slate-600">
        Щоб додати або перемістити пристрій — відкрий «Мій дім → Редагувати»
      </p>
    </div>
  );
}
