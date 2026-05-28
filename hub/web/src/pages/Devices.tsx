import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../lib/api";
import { Dialog } from "../components/Dialog";
import { Button } from "../components/Button";
import { Spinner } from "../components/Spinner";
import type { DeviceRow, DeviceUpdate } from "../lib/types";

const KIND_ICON: Record<string, string> = {
  camera: "📷", light: "💡", lock: "🔒", thermostat: "🌡",
  relay: "⚡", sensor_pir: "👁", sensor_door: "🚪",
  sensor_dht: "💧", sensor_mq2: "💨", sensor_power: "🔌", speaker: "🔊",
};

const ALL_ACTIONS = ["on", "off", "toggle", "brightness_set", "temp_set"];
const ACTION_LABEL: Record<string, string> = {
  on: "Увімкнути", off: "Вимкнути", toggle: "Перемкнути",
  brightness_set: "Яскравість", temp_set: "Температура",
};

// ── Chip input for editing list of strings ────────────────────────────────

function ChipInput({
  values,
  onChange,
  placeholder,
}: {
  values: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [input, setInput] = useState("");

  const add = () => {
    const v = input.trim();
    if (v && !values.includes(v)) onChange([...values, v]);
    setInput("");
  };

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-1 min-h-8">
        {values.map((v) => (
          <span
            key={v}
            className="inline-flex items-center gap-1 bg-slate-700 text-slate-200 text-xs px-2 py-0.5 rounded-full"
          >
            {v}
            <button
              type="button"
              onClick={() => onChange(values.filter((x) => x !== v))}
              className="text-slate-400 hover:text-red-400"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          className="flex-1 bg-slate-700 text-sm text-slate-100 rounded-lg px-3 py-1.5 border border-slate-600 focus:outline-none focus:border-blue-500"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())}
          placeholder={placeholder ?? "Введіть та натисніть Enter"}
        />
        <Button size="sm" onClick={add} type="button">+</Button>
      </div>
    </div>
  );
}

// ── Edit dialog ───────────────────────────────────────────────────────────

interface EditDialogProps {
  device: DeviceRow | null;
  onClose: () => void;
}

function EditDialog({ device, onClose }: EditDialogProps) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<DeviceUpdate>({});

  // Reset form when device changes
  const d = device;
  const label = form.label !== undefined ? form.label : d?.label ?? "";
  const aliases = form.aliases ?? d?.aliases ?? [];
  const controllable = form.controllable !== undefined ? form.controllable : d?.controllable ?? false;
  const actions = form.actions ?? d?.actions ?? [];
  const mqttTopic = (form.config?.mqtt_topic as string | undefined)
    ?? (d?.config?.mqtt_topic as string | undefined)
    ?? "";
  const payloadOn = (form.config?.payload_on !== undefined
    ? JSON.stringify(form.config.payload_on, null, 2)
    : d?.config?.payload_on
      ? JSON.stringify(d.config.payload_on, null, 2)
      : '{"state":"on"}');
  const payloadOff = (form.config?.payload_off !== undefined
    ? JSON.stringify(form.config.payload_off, null, 2)
    : d?.config?.payload_off
      ? JSON.stringify(d.config.payload_off, null, 2)
      : '{"state":"off"}');

  const mutation = useMutation({
    mutationFn: (body: DeviceUpdate) =>
      api.patch<DeviceRow>(`/api/devices/${d?.device_id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      toast.success("Збережено");
      onClose();
    },
  });

  const handleSave = () => {
    let payload_on: unknown;
    let payload_off: unknown;
    try { payload_on = JSON.parse(payloadOn); } catch { payload_on = { state: "on" }; }
    try { payload_off = JSON.parse(payloadOff); } catch { payload_off = { state: "off" }; }

    mutation.mutate({
      label: label || null,
      aliases,
      controllable,
      actions,
      config: {
        ...d?.config,
        ...(mqttTopic ? { mqtt_topic: mqttTopic } : {}),
        payload_on,
        payload_off,
      },
    });
  };

  if (!d) return null;

  return (
    <Dialog
      open={!!d}
      onOpenChange={(o) => !o && onClose()}
      title={`Редагувати: ${d.label ?? d.device_id}`}
    >
      <div className="space-y-4 text-sm">
        {/* Label */}
        <div>
          <label className="block text-slate-400 mb-1">Назва</label>
          <input
            className="w-full bg-slate-700 text-slate-100 rounded-lg px-3 py-1.5 border border-slate-600 focus:outline-none focus:border-blue-500"
            value={label ?? ""}
            onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
          />
        </div>

        {/* Aliases */}
        <div>
          <label className="block text-slate-400 mb-1">Голосові аліаси (UA)</label>
          <ChipInput
            values={aliases}
            onChange={(v) => setForm((f) => ({ ...f, aliases: v }))}
            placeholder='"люстра", "лампа на стелі"…'
          />
        </div>

        {/* Controllable */}
        <label className="flex items-center gap-3 cursor-pointer select-none">
          <input
            type="checkbox"
            className="w-4 h-4 accent-blue-500"
            checked={controllable}
            onChange={(e) => setForm((f) => ({ ...f, controllable: e.target.checked }))}
          />
          <span className="text-slate-200">Керований голосом</span>
        </label>

        {/* Actions */}
        <div>
          <label className="block text-slate-400 mb-1">Підтримувані дії</label>
          <div className="flex flex-wrap gap-2">
            {ALL_ACTIONS.map((a) => (
              <label key={a} className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-blue-500"
                  checked={actions.includes(a)}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      actions: e.target.checked
                        ? [...actions, a]
                        : actions.filter((x) => x !== a),
                    }))
                  }
                />
                <span className="text-slate-300">{ACTION_LABEL[a]}</span>
              </label>
            ))}
          </div>
        </div>

        {/* MQTT topic */}
        <div>
          <label className="block text-slate-400 mb-1">
            MQTT топік команди{" "}
            <span className="text-slate-600 text-xs">
              (залишіть порожнім для автоматичного)
            </span>
          </label>
          <input
            className="w-full bg-slate-700 text-slate-100 rounded-lg px-3 py-1.5 border border-slate-600 focus:outline-none focus:border-blue-500 font-mono text-xs"
            value={mqttTopic}
            placeholder={`home/${d.room_slug}/${d.kind}/cmd`}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                config: { ...(f.config ?? {}), mqtt_topic: e.target.value },
              }))
            }
          />
        </div>

        {/* Payload on */}
        <div>
          <label className="block text-slate-400 mb-1">Payload ON (JSON)</label>
          <textarea
            rows={2}
            className="w-full bg-slate-700 text-slate-100 rounded-lg px-3 py-1.5 border border-slate-600 focus:outline-none focus:border-blue-500 font-mono text-xs"
            defaultValue={payloadOn}
            onChange={(e) => {
              try {
                const parsed = JSON.parse(e.target.value);
                setForm((f) => ({ ...f, config: { ...(f.config ?? {}), payload_on: parsed } }));
              } catch {
                /* ignore invalid JSON while typing */
              }
            }}
          />
        </div>

        {/* Payload off */}
        <div>
          <label className="block text-slate-400 mb-1">Payload OFF (JSON)</label>
          <textarea
            rows={2}
            className="w-full bg-slate-700 text-slate-100 rounded-lg px-3 py-1.5 border border-slate-600 focus:outline-none focus:border-blue-500 font-mono text-xs"
            defaultValue={payloadOff}
            onChange={(e) => {
              try {
                const parsed = JSON.parse(e.target.value);
                setForm((f) => ({ ...f, config: { ...(f.config ?? {}), payload_off: parsed } }));
              } catch {
                /* ignore invalid JSON while typing */
              }
            }}
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose}>Скасувати</Button>
          <Button variant="primary" onClick={handleSave} disabled={mutation.isPending}>
            {mutation.isPending ? "Збереження…" : "Зберегти"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────

export default function Devices() {
  const { data: devices, isLoading, error } = useQuery<DeviceRow[]>({
    queryKey: ["devices"],
    queryFn: () => api.get<DeviceRow[]>("/api/devices"),
  });

  const [editing, setEditing] = useState<DeviceRow | null>(null);
  const [testing, setTesting] = useState<string | null>(null);

  const handleTest = async (device: DeviceRow) => {
    setTesting(device.device_id);
    try {
      const result = await api.post<{ result: string }>(
        `/api/devices/${device.device_id}/command`,
        { payload: { state: "on" }, intent_text: "UI тест" },
      );
      if (result.result === "auto_executed") toast.success("Команду відправлено!");
      else if (result.result === "confirm_required") toast.info("Потрібне підтвердження");
      else toast.warning(result.result);
    } catch {
      /* toast shown by api helper */
    } finally {
      setTesting(null);
    }
  };

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>;
  if (error) return <p className="text-red-400">Помилка завантаження пристроїв</p>;

  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">Пристрої</h1>

      {(!devices || devices.length === 0) ? (
        <p className="text-slate-400">
          Немає пристроїв. Додайте їх через редактор плану поверху.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-700">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-400 border-b border-slate-700 bg-slate-800/50">
                <th className="px-4 py-2">Кімната</th>
                <th className="px-4 py-2">Тип</th>
                <th className="px-4 py-2">Назва</th>
                <th className="px-4 py-2">Аліаси</th>
                <th className="px-4 py-2 text-center">Голос</th>
                <th className="px-4 py-2">Дії</th>
                <th className="px-4 py-2">MQTT топік</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => {
                const topic =
                  (d.config.mqtt_topic as string | undefined) ??
                  `home/${d.room_slug}/${d.kind}/cmd`;
                return (
                  <tr
                    key={d.id}
                    className="border-b border-slate-800 hover:bg-slate-800/30 transition-colors"
                  >
                    <td className="px-4 py-2 text-slate-300">{d.room_name}</td>
                    <td className="px-4 py-2">
                      <span title={d.kind}>{KIND_ICON[d.kind] ?? "❓"} {d.kind}</span>
                    </td>
                    <td className="px-4 py-2 text-slate-200">{d.label ?? <span className="text-slate-500 italic">—</span>}</td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1">
                        {d.aliases.length === 0
                          ? <span className="text-slate-600 italic text-xs">немає</span>
                          : d.aliases.map((a) => (
                            <span key={a} className="bg-slate-700 text-slate-300 text-xs px-2 py-0.5 rounded-full">{a}</span>
                          ))}
                      </div>
                    </td>
                    <td className="px-4 py-2 text-center">
                      {d.controllable
                        ? <span className="text-green-400">✓</span>
                        : <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1">
                        {d.actions.map((a) => (
                          <span key={a} className="bg-slate-700/50 text-slate-400 text-xs px-1.5 py-0.5 rounded">{a}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-500 max-w-[180px] truncate" title={topic}>
                      {topic}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => setEditing(d)}>✏️</Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={testing === d.device_id}
                          onClick={() => handleTest(d)}
                          title="Відправити ON команду"
                        >
                          {testing === d.device_id ? "…" : "▶"}
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <EditDialog device={editing} onClose={() => setEditing(null)} />
    </div>
  );
}
