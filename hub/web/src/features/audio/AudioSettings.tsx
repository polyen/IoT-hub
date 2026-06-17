import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Mic, Volume2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../lib/api";

interface AudioDevice {
  id: string;
  name: string;
  type: "rtsp_mic" | "local_mic" | "rtsp_speaker" | "local_speaker";
  available: boolean;
}
interface AudioConfig {
  input_id: string | null;
  output_id: string | null;
}

export function AudioSettings() {
  const qc = useQueryClient();

  const { data: devices = [], isLoading: devLoading } = useQuery<AudioDevice[]>({
    queryKey: ["audio-devices"],
    queryFn: () => api.get<AudioDevice[]>("/api/audio/devices"),
  });
  const { data: config } = useQuery<AudioConfig>({
    queryKey: ["audio-config"],
    queryFn: () => api.get<AudioConfig>("/api/audio/config"),
  });

  const save = useMutation({
    mutationFn: (body: AudioConfig) => api.put<AudioConfig>("/api/audio/config", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["audio-config"] });
      toast.success("Аудіо налаштування збережено — пайплайн перемикається");
    },
    onError: () => toast.error("Не вдалось зберегти"),
  });

  const mics = devices.filter((d) => d.type === "rtsp_mic" || d.type === "local_mic");
  const speakers = devices.filter((d) => d.type === "rtsp_speaker" || d.type === "local_speaker");

  if (devLoading) return <div className="text-slate-400 text-sm">Завантаження пристроїв…</div>;

  return (
    <div className="rounded-xl border border-slate-700 light:border-slate-200 bg-slate-800/50 light:bg-white p-4 space-y-4">
      <p className="text-sm font-semibold text-slate-200 light:text-slate-800">Аудіо пристрої</p>

      {/* Microphone */}
      <div className="space-y-1.5">
        <label className="flex items-center gap-1.5 text-xs text-slate-400">
          <Mic size={13} /> Мікрофон (вхід)
        </label>
        <select
          className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-primary-500"
          value={config?.input_id ?? ""}
          onChange={(e) => save.mutate({ input_id: e.target.value || null, output_id: config?.output_id ?? null })}
        >
          <option value="">— не обрано —</option>
          {mics.map((d) => (
            <option key={d.id} value={d.id} disabled={!d.available}>
              {d.name}{!d.available ? " (недоступний)" : ""}
            </option>
          ))}
        </select>
      </div>

      {/* Speaker */}
      <div className="space-y-1.5">
        <label className="flex items-center gap-1.5 text-xs text-slate-400">
          <Volume2 size={13} /> Динамік (вихід)
        </label>
        <select
          className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-primary-500"
          value={config?.output_id ?? ""}
          onChange={(e) => save.mutate({ input_id: config?.input_id ?? null, output_id: e.target.value || null })}
        >
          <option value="">— не обрано —</option>
          {speakers.map((d) => (
            <option key={d.id} value={d.id} disabled={!d.available}>
              {d.name}{!d.available ? " (недоступний)" : ""}
            </option>
          ))}
        </select>
      </div>

      <p className="text-xs text-slate-500 flex items-center gap-1">
        <RefreshCw size={10} />
        Зміна застосовується без перезапуску — voice pipeline переключається автоматично
      </p>
    </div>
  );
}
