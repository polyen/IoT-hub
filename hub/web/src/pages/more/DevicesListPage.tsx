import { useState } from "react";
import { useForm } from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { useDecideConfirm } from "../../features/confirm/useDecideConfirm";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { countdownSeconds } from "../../lib/format";
import type { ConfirmRequest, DeviceKind, DevicePlacement, FloorPlanData, Room } from "../../lib/types";

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

const ACTIONS_BY_KIND: Record<string, string[]> = {
  light: ["on", "off", "toggle", "brightness_set"],
  relay: ["on", "off", "toggle"],
  lock: ["open", "close"],
  thermostat: ["set", "temp_set", "inc", "dec"],
};

const ACTION_LABELS: Record<string, string> = {
  on: "Увімкнути",
  off: "Вимкнути",
  toggle: "Перемкнути",
  open: "Відкрити",
  close: "Закрити",
  set: "Встановити",
  inc: "Збільшити",
  dec: "Зменшити",
  brightness_set: "Яскравість",
  temp_set: "Температура",
};

interface EditForm {
  label: string;
  device_id: string;
  kind: DeviceKind;
  mqtt_topic: string;
  controllable: boolean;
  actions: string[];
}

/* Inline confirm card shown when a device command returns confirm_required */
function InlineConfirmCard({
  confirmId,
  deviceLabel,
  onDone,
}: {
  confirmId: string;
  deviceLabel: string;
  onDone: () => void;
}) {
  const { data: req } = useQuery<ConfirmRequest>({
    queryKey: ["confirm", confirmId],
    queryFn: () => api.get<ConfirmRequest>(`/api/confirm/${confirmId}`),
    refetchInterval: 3_000,
  });

  const { mutate: decide, isPending } = useDecideConfirm();
  const seconds = req ? countdownSeconds(req.expires_at) : 60;
  const pct = Math.max(0, (seconds / 60) * 100);

  const handle = (decision: "approve" | "reject") => {
    if ("vibrate" in navigator) navigator.vibrate(decision === "approve" ? [50] : [50, 50, 50]);
    decide({ id: confirmId, decision }, { onSuccess: onDone });
  };

  return (
    <div className="mt-2 rounded-lg border border-amber-700 bg-amber-950/40 p-3 space-y-2">
      <div className="h-1 rounded-full bg-slate-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-1000 ${
            pct > 50 ? "bg-green-500" : pct > 20 ? "bg-amber-500" : "bg-red-500"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-amber-300">
        {req?.confirm_message ?? `Підтверди дію для ${deviceLabel}`}
        <span className="ml-2 font-mono text-slate-400">{seconds}с</span>
      </p>
      <div className="flex gap-2">
        <Button variant="primary" size="sm" className="flex-1" disabled={isPending || seconds === 0} onClick={() => handle("approve")}>
          Схвалити
        </Button>
        <Button variant="danger" size="sm" className="flex-1" disabled={isPending} onClick={() => handle("reject")}>
          Відхилити
        </Button>
      </div>
    </div>
  );
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
  const [pendingConfirmId, setPendingConfirmId] = useState<string | null>(null);

  const form = useForm<EditForm>({
    defaultValues: {
      label: placement.label ?? "",
      device_id: placement.device_id,
      kind: placement.kind as DeviceKind,
      mqtt_topic: (placement.config.mqtt_topic as string) ?? "",
      controllable: placement.controllable ?? false,
      actions: placement.actions ?? [],
    },
  });

  const commandMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.post<{ result: string; confirm_id?: string }>(
        `/api/devices/${placement.device_id}/command`,
        { payload, intent_text: "UI command" },
      ),
    onSuccess: (data) => {
      if (data.result === "confirm_required" && data.confirm_id) {
        setPendingConfirmId(data.confirm_id);
        toast.info("Потрібне підтвердження");
      } else if (data.result === "auto_executed") {
        toast.success("Виконано");
      }
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
        controllable: data.controllable,
        actions: data.controllable ? data.actions : [],
      });
      setExpanded(false);
    } finally {
      setSaving(false);
    }
  }

  const actionKinds = ["light", "relay", "lock"] as const;
  const isControllable = actionKinds.includes(placement.kind as (typeof actionKinds)[number]);

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="text-2xl w-8 shrink-0 text-center" aria-hidden>
          {KIND_ICON[placement.kind] ?? "⚙"}
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-sm truncate">{placement.label || placement.device_id}</p>
          <p className="text-xs text-slate-500 truncate">
            {KIND_LABELS[placement.kind] ?? placement.kind}
            {room ? ` · ${room.name}` : ""}
            {" · "}<span className="font-mono">{placement.device_id}</span>
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {isControllable && !expanded && (
            <>
              {placement.kind === "light" && (
                <>
                  <Button size="sm" disabled={commandMutation.isPending} onClick={() => commandMutation.mutate({ state: "on" })}>Вкл</Button>
                  <Button size="sm" variant="ghost" disabled={commandMutation.isPending} onClick={() => commandMutation.mutate({ state: "off" })}>Викл</Button>
                </>
              )}
              {placement.kind === "relay" && (
                <>
                  <Button size="sm" disabled={commandMutation.isPending} onClick={() => commandMutation.mutate({ cmd: "relay_on" })}>Вкл</Button>
                  <Button size="sm" variant="ghost" disabled={commandMutation.isPending} onClick={() => commandMutation.mutate({ cmd: "relay_off" })}>Викл</Button>
                </>
              )}
              {placement.kind === "lock" && (
                <Button size="sm" variant="danger" disabled={commandMutation.isPending} onClick={() => commandMutation.mutate({ action: "unlock" })}>
                  Відкрити
                </Button>
              )}
            </>
          )}
          <button
            onClick={() => setExpanded((v) => !v)}
            className="rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-700 hover:text-white"
          >
            {expanded ? "Закрити" : "Редагувати"}
          </button>
        </div>
      </div>

      {/* Inline confirm card */}
      {pendingConfirmId && (
        <div className="px-4 pb-3">
          <InlineConfirmCard
            confirmId={pendingConfirmId}
            deviceLabel={placement.label || placement.device_id}
            onDone={() => setPendingConfirmId(null)}
          />
        </div>
      )}

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

          {/* Voice control */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              {...form.register("controllable")}
              className="h-4 w-4 rounded border-slate-600 bg-slate-700 text-blue-500 focus:ring-blue-500"
            />
            <span className="text-sm text-slate-300">Керується голосом</span>
          </label>

          {form.watch("controllable") && ACTIONS_BY_KIND[form.watch("kind")] && (
            <div className="space-y-1">
              <span className="block text-xs text-slate-400">Доступні дії</span>
              <div className="flex flex-wrap gap-2">
                {ACTIONS_BY_KIND[form.watch("kind")].map((action) => (
                  <label key={action} className="flex items-center gap-1.5 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      value={action}
                      checked={form.watch("actions").includes(action)}
                      onChange={(e) => {
                        const prev = form.getValues("actions");
                        form.setValue(
                          "actions",
                          e.target.checked ? [...prev, action] : prev.filter((a) => a !== action),
                        );
                      }}
                      className="h-4 w-4 rounded border-slate-600 bg-slate-700 text-blue-500 focus:ring-blue-500"
                    />
                    <span className="text-xs text-slate-300">{ACTION_LABELS[action] ?? action}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center gap-2 pt-1">
            <Button type="submit" size="sm" variant="primary" disabled={saving}>
              {saving ? "Зберігаємо…" : "Зберегти"}
            </Button>
            <Button type="button" size="sm" variant="secondary" onClick={() => { form.reset(); setExpanded(false); }}>
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
    mutationFn: (updated: FloorPlanData) => api.put<FloorPlanData>("/api/floorplan", updated, false, 30_000),
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
              <DeviceCard key={p.id} placement={p} room={room} onSave={handleSave} onDelete={handleDelete} />
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
