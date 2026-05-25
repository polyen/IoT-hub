import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Mic, Volume2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { shortDateTime, relativeTime } from "../../lib/format";
import type { AgentAuditEntry } from "../../lib/types";

type Tab = "transcripts" | "try" | "audit" | "stack";

const ACTION_COLORS: Record<string, string> = {
  AUTO: "bg-green-900/60 text-green-300",
  CONFIRM: "bg-amber-900/60 text-amber-300",
  DENY: "bg-red-900/60 text-red-300",
};

interface VoiceMessage {
  type: "transcript" | "wakeword";
  text: string;
  ts: string;
  confidence?: number;
}

// ── Push-to-Talk ────────────────────────────────────────────────────────────

function PushToTalk() {
  const [recording, setRecording] = useState(false);
  const [uploading, setUploading] = useState(false);
  const mrRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  async function startRecording() {
    if (recording) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/ogg";
      const mr = new MediaRecorder(stream, { mimeType });
      chunks.current = [];
      mr.ondataavailable = (e) => { if (e.data.size > 0) chunks.current.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks.current, { type: mimeType });
        setUploading(true);
        try {
          await fetch("/api/agent/voice/audio", {
            method: "POST",
            body: blob,
            headers: { "Content-Type": mimeType },
          });
          toast.success("Аудіо відправлено у пайплайн");
        } catch {
          toast.error("Не вдалося відправити аудіо");
        } finally {
          setUploading(false);
        }
      };
      mr.start(100); // collect in 100ms chunks
      mrRef.current = mr;
      setRecording(true);
      if ("vibrate" in navigator) navigator.vibrate(50);
    } catch {
      toast.error("Немає доступу до мікрофона");
    }
  }

  function stopRecording() {
    if (!recording || !mrRef.current) return;
    mrRef.current.stop();
    mrRef.current = null;
    setRecording(false);
    if ("vibrate" in navigator) navigator.vibrate([30, 30, 30]);
  }

  return (
    <div className="flex flex-col items-center gap-3 py-6">
      <button
        onPointerDown={startRecording}
        onPointerUp={stopRecording}
        onPointerLeave={stopRecording}
        disabled={uploading}
        className={[
          "w-20 h-20 rounded-full text-2xl transition-all duration-150 select-none touch-none",
          "border-4 shadow-lg active:scale-95",
          recording
            ? "bg-red-600 border-red-400 animate-pulse"
            : "bg-slate-700 border-slate-500 hover:bg-slate-600",
          uploading ? "opacity-50 cursor-wait" : "cursor-pointer",
        ].join(" ")}
        aria-label={recording ? "Зупинити запис" : "Утримуй для запису"}
      >
        {uploading ? "⏳" : recording ? "🔴" : "🎤"}
      </button>
      <p className="text-xs text-slate-500">
        {uploading ? "Відправляємо…" : recording ? "Запис… відпусти щоб відправити" : "Утримуй для запису"}
      </p>
    </div>
  );
}

// ── Transcripts tab ─────────────────────────────────────────────────────────

function TranscriptsTab() {
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    function connect() {
      const ws = new WebSocket(`${proto}://${location.host}/api/agent/ws/voice`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false); setTimeout(connect, 3000); };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try {
          const msg: VoiceMessage = JSON.parse(e.data as string);
          setMessages((prev) => [msg, ...prev].slice(0, 100));
        } catch { /* ignore */ }
      };
    }
    connect();
    return () => wsRef.current?.close();
  }, []);

  return (
    <div className="space-y-4">
      <PushToTalk />

      <div className="flex items-center gap-2 text-sm">
        <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-500" : "bg-slate-500"}`} />
        <span className="text-slate-400">{connected ? "Підключено" : "Очікування підключення…"}</span>
      </div>

      {messages.length === 0 ? (
        <div className="py-8 text-center text-slate-500">
          <p className="text-sm">Очікування голосових подій…</p>
          <p className="text-xs mt-1 text-slate-600">Скажіть ключове слово або скористайтесь кнопкою вище</p>
        </div>
      ) : (
        <div className="space-y-2">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`rounded-lg px-4 py-3 text-sm border ${
                msg.type === "wakeword"
                  ? "border-blue-700 bg-blue-900/30 text-blue-200"
                  : "border-slate-700 bg-slate-800/60"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <p className="flex-1">{msg.text}</p>
                <span className="shrink-0 text-xs text-slate-500">{relativeTime(msg.ts)}</span>
              </div>
              {msg.confidence !== undefined && (
                <p className="mt-1 text-xs text-slate-500">
                  {msg.type === "wakeword" ? "Ключове слово" : "Транскрипція"} · {Math.round(msg.confidence * 100)}%
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Try / Simulator tab ─────────────────────────────────────────────────────

interface TryResult {
  matched_rule: string;
  action_class: string;
  reason: string;
  latency_ms: number;
  inferred_tool?: string | null;
}

function TryTab() {
  const [intentText, setIntentText] = useState("");
  const [tool, setTool] = useState("");
  const [result, setResult] = useState<TryResult | null>(null);

  const tryMutation = useMutation({
    mutationFn: (body: { intent_text: string; tool?: string }) =>
      api.post<TryResult>("/api/agent/try", body),
    onSuccess: (data) => setResult(data),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!intentText.trim()) return;
    tryMutation.mutate({ intent_text: intentText, tool: tool || undefined });
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-400">
        Перевір як намір або інструмент буде класифіковано політикою безпеки.
      </p>
      <form onSubmit={handleSubmit} className="space-y-3">
        <label className="block space-y-1">
          <span className="text-xs text-slate-400">Намір (intent_text)</span>
          <textarea
            value={intentText}
            onChange={(e) => setIntentText(e.target.value)}
            rows={3}
            placeholder="напр. «Вимкни всі лампи в будинку»"
            className="w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-xs text-slate-400">Інструмент (необов'язково)</span>
          <input
            value={tool}
            onChange={(e) => setTool(e.target.value)}
            placeholder="напр. mqtt_publish"
            className="w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </label>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={tryMutation.isPending || !intentText.trim()}
        >
          {tryMutation.isPending ? "Перевіряємо…" : "Симулювати"}
        </Button>
      </form>

      {result && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-2 text-sm">
          <div className="flex items-center gap-3">
            <span className={`rounded px-2 py-0.5 text-xs font-bold ${ACTION_COLORS[result.action_class] ?? "bg-slate-700 text-slate-300"}`}>
              {result.action_class}
            </span>
            <span className="text-slate-400">{result.latency_ms} мс</span>
            {result.inferred_tool && (
              <span className="rounded bg-slate-700 px-2 py-0.5 text-xs text-slate-300">
                ↳ <span className="font-mono">{result.inferred_tool}</span> (інференс)
              </span>
            )}
          </div>
          <p className="text-slate-300"><span className="text-slate-500">Правило: </span>{result.matched_rule}</p>
          <p className="text-slate-400 text-xs">{result.reason}</p>
        </div>
      )}
    </div>
  );
}

// ── Audit tab ───────────────────────────────────────────────────────────────

function exportCSV(data: AgentAuditEntry[]) {
  const headers = ["timestamp", "action_class", "tool", "intent_text", "latency_ms", "confirmation"];
  const rows = data.map((e) => [
    e.timestamp,
    e.action_class,
    e.tool ?? "",
    e.intent_text,
    e.latency_ms ?? "",
    e.confirmation ?? "",
  ]);
  const csv = [headers, ...rows]
    .map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","))
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `agent-audit-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function AuditTab() {
  const { data, isLoading } = useQuery<AgentAuditEntry[]>({
    queryKey: ["agent-audit"],
    queryFn: () => api.get<AgentAuditEntry[]>("/api/agent/audit"),
    refetchInterval: 15_000,
  });

  if (isLoading) return <div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div>;

  return (
    <div className="space-y-3">
      {data && data.length > 0 && (
        <div className="flex justify-end">
          <button
            onClick={() => exportCSV(data)}
            className="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1"
          >
            ↓ CSV
          </button>
        </div>
      )}

      {!data?.length ? (
        <div className="py-12 text-center text-slate-500">
          <p className="text-sm">Записів аудиту ще немає</p>
        </div>
      ) : (
        <div className="space-y-2">
          {data.map((entry) => (
            <div key={entry.id} className="rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3">
              <div className="flex items-start gap-2">
                <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-xs font-bold ${ACTION_COLORS[entry.action_class] ?? "bg-slate-700"}`}>
                  {entry.action_class}
                </span>
                <p className="flex-1 text-sm text-slate-200 leading-snug">{entry.intent_text}</p>
                <span className="shrink-0 text-xs text-slate-500">{shortDateTime(entry.timestamp)}</span>
              </div>
              {(entry.tool || entry.latency_ms) && (
                <p className="mt-1 pl-10 text-xs text-slate-500">
                  {entry.tool && <span className="font-mono">{entry.tool} · </span>}
                  {entry.latency_ms != null && <span>{entry.latency_ms} мс</span>}
                  {entry.confirmation && <span className="ml-2">{entry.confirmation}</span>}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Stack tab — live agent turn stream ──────────────────────────────────────

interface AgentTurnEvent {
  type: "intent" | "tool_call" | "result" | "ping";
  text?: string;
  tool?: string;
  topic?: string;
  payload?: unknown;
  action_class?: "AUTO" | "CONFIRM" | "DENY" | "ERROR";
  class_?: string;      // routing class for intent events
  score?: number;
  prototype?: string;
  ts?: string;
}

const CLASS_BADGE: Record<string, string> = {
  AUTO: "bg-green-900/70 text-green-300",
  CONFIRM: "bg-amber-900/70 text-amber-300",
  DENY: "bg-red-900/70 text-red-300",
  ERROR: "bg-red-900/70 text-red-400",
  DETERMINISTIC: "bg-blue-900/70 text-blue-300",
  STRUCTURED: "bg-purple-900/70 text-purple-300",
  CREATIVE: "bg-pink-900/70 text-pink-300",
  UNKNOWN: "bg-slate-800 text-slate-400",
};

const TYPE_CONFIG: Record<string, { icon: string; border: string; bg: string; label: string }> = {
  intent:    { icon: "💬", border: "border-blue-800/60",   bg: "bg-blue-950/30",   label: "Намір" },
  tool_call: { icon: "⚙️",  border: "border-amber-800/60", bg: "bg-amber-950/30",  label: "Виклик" },
  result:    { icon: "✓",  border: "border-green-800/60", bg: "bg-green-950/30",  label: "Результат" },
};

function EventCard({ ev }: { ev: AgentTurnEvent }) {
  const [expanded, setExpanded] = useState(false);
  const cfg = TYPE_CONFIG[ev.type] ?? { icon: "•", border: "border-slate-700", bg: "bg-slate-800/60", label: ev.type };
  const badge = ev.action_class ?? ev.class_;
  const hasPayload = ev.payload != null && typeof ev.payload === "object" && Object.keys(ev.payload as object).length > 0;

  return (
    <div className={`rounded-lg border ${cfg.border} ${cfg.bg} px-3 py-2 text-xs`}>
      <div className="flex items-start gap-2">
        <span className="text-base leading-none mt-0.5 select-none">{cfg.icon}</span>
        <div className="flex-1 min-w-0 space-y-0.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-semibold text-white/80">{cfg.label}</span>
            {badge && (
              <span className={`rounded px-1.5 py-px text-[10px] font-bold ${CLASS_BADGE[badge] ?? "bg-slate-700 text-slate-300"}`}>
                {badge}
              </span>
            )}
            {ev.prototype && (
              <span className="text-slate-500 font-mono">{ev.prototype}</span>
            )}
            {ev.score != null && (
              <span className="text-slate-600">{(ev.score * 100).toFixed(0)}%</span>
            )}
          </div>

          {ev.text && (
            <p className="text-white/70 leading-snug"
               style={{ display: "-webkit-box", WebkitLineClamp: expanded ? undefined : 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
              {ev.text}
            </p>
          )}
          {ev.tool && (
            <p className="font-mono text-amber-300/80">{ev.tool}{ev.topic ? ` → ${ev.topic}` : ""}</p>
          )}

          {hasPayload && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-slate-500 hover:text-slate-300 mt-0.5"
            >
              {expanded ? "▲ скрити" : "▼ payload"}
            </button>
          )}
          {expanded && hasPayload && (
            <pre className="mt-1 rounded bg-black/30 p-2 text-[10px] text-slate-300 overflow-x-auto whitespace-pre-wrap break-all">
              {JSON.stringify(ev.payload, null, 2)}
            </pre>
          )}
        </div>

        {ev.ts && (
          <span className="shrink-0 text-slate-600 font-mono tabular-nums">
            {new Date(ev.ts).toLocaleTimeString("uk", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </span>
        )}
      </div>
    </div>
  );
}

function StackTab() {
  const [events, setEvents] = useState<AgentTurnEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    function connect() {
      const ws = new WebSocket(`${proto}://${location.host}/api/agent/ws/agent`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false); setTimeout(connect, 5000); };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try {
          const ev: AgentTurnEvent = JSON.parse(e.data as string);
          if (ev.type === "ping") return;
          setEvents((prev) => [...prev.slice(-199), ev]);
          if (autoScrollRef.current) {
            requestAnimationFrame(() => {
              listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
            });
          }
        } catch { /* ignore */ }
      };
    }
    connect();
    return () => wsRef.current?.close();
  }, []);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-500 animate-pulse" : "bg-slate-500"}`} />
          <span className="text-slate-400">{connected ? "Агент підключений" : "Очікування агента…"}</span>
        </div>
        {events.length > 0 && (
          <button onClick={() => setEvents([])} className="text-xs text-slate-500 hover:text-slate-300">
            Очистити
          </button>
        )}
      </div>

      <div
        ref={listRef}
        className="space-y-1.5 max-h-[65vh] overflow-y-auto pr-1"
        onScroll={(e) => {
          const el = e.currentTarget;
          autoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}
      >
        {events.length === 0 ? (
          <div className="py-14 text-center text-slate-500">
            <p className="text-3xl mb-3">🤖</p>
            <p className="text-sm">Агент очікує команди</p>
            <p className="text-xs mt-1 text-slate-600">
              Скажи команду або відправ через «Сценарії» — тут з'явиться хід обробки
            </p>
          </div>
        ) : (
          events.map((ev, i) => <EventCard key={i} ev={ev} />)
        )}
      </div>
    </div>
  );
}

// ── Main page ───────────────────────────────────────────────────────────────

// ── Audio device selector ────────────────────────────────────────────────────

interface AudioDevice {
  id: string;
  name: string;
  type: "rtsp_mic" | "local_mic" | "rtsp_speaker" | "local_speaker";
  available: boolean;
}
interface AudioConfig { input_id: string | null; output_id: string | null; }

function AudioSettings() {
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
    <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-4 space-y-4">
      <p className="text-sm font-semibold text-slate-200">Аудіо пристрої</p>

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

      <p className="text-[11px] text-slate-500 flex items-center gap-1">
        <RefreshCw size={10} />
        Зміна застосовується без перезапуску — voice pipeline переключається автоматично
      </p>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function VoicePage() {
  const [tab, setTab] = useState<Tab>("transcripts");

  const tabs: { id: Tab; label: string }[] = [
    { id: "transcripts", label: "Транскрипції" },
    { id: "try", label: "Симулятор" },
    { id: "audit", label: "Аудит" },
    { id: "stack", label: "Стек" },
  ];

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Голосовий асистент</h1>

      <AudioSettings />

      <div className="flex gap-0.5 rounded-lg border border-slate-700 bg-slate-800/60 p-1 w-fit overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={[
              "rounded px-4 py-1.5 text-sm font-medium transition-colors whitespace-nowrap",
              tab === t.id
                ? "bg-slate-700 text-white"
                : "text-slate-400 hover:text-white",
            ].join(" ")}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "transcripts" && <TranscriptsTab />}
      {tab === "try" && <TryTab />}
      {tab === "audit" && <AuditTab />}
      {tab === "stack" && <StackTab />}
    </div>
  );
}
