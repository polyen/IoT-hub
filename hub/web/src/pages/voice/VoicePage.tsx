import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { shortDateTime, relativeTime } from "../../lib/format";
import type { AgentAuditEntry } from "../../lib/types";

type Tab = "transcripts" | "try" | "audit";

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
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm">
        <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-500" : "bg-slate-500"}`} />
        <span className="text-slate-400">{connected ? "Підключено" : "Очікування підключення…"}</span>
      </div>

      {messages.length === 0 ? (
        <div className="py-12 text-center text-slate-500">
          <p className="text-3xl mb-2">🎤</p>
          <p className="text-sm">Очікування голосових подій…</p>
          <p className="text-xs mt-1 text-slate-600">Скажіть ключове слово щоб активувати асистента</p>
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
                  {msg.type === "wakeword" ? "Ключове слово" : "Транскрипція"} · впевненість {Math.round(msg.confidence * 100)}%
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface TryResult {
  matched_rule: string;
  action_class: string;
  reason: string;
  latency_ms: number;
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
        <Button type="submit" variant="primary" size="sm" disabled={tryMutation.isPending || !intentText.trim()}>
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
          </div>
          <p className="text-slate-300"><span className="text-slate-500">Правило: </span>{result.matched_rule}</p>
          <p className="text-slate-400 text-xs">{result.reason}</p>
        </div>
      )}
    </div>
  );
}

function AuditTab() {
  const { data, isLoading } = useQuery<AgentAuditEntry[]>({
    queryKey: ["agent-audit"],
    queryFn: () => api.get<AgentAuditEntry[]>("/api/agent/audit"),
    refetchInterval: 15_000,
  });

  if (isLoading) return <div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div>;
  if (!data?.length) return (
    <div className="py-12 text-center text-slate-500">
      <p className="text-sm">Записів аудиту ще немає</p>
    </div>
  );

  return (
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
  );
}

export default function VoicePage() {
  const [tab, setTab] = useState<Tab>("transcripts");

  const tabs: { id: Tab; label: string }[] = [
    { id: "transcripts", label: "Транскрипції" },
    { id: "try", label: "Симулятор" },
    { id: "audit", label: "Аудит" },
  ];

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Голосовий асистент</h1>

      {/* Tabs */}
      <div className="flex gap-0.5 rounded-lg border border-slate-700 bg-slate-800/60 p-1 w-fit">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={[
              "rounded px-4 py-1.5 text-sm font-medium transition-colors",
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
    </div>
  );
}
