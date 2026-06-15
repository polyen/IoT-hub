import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { AgentTurnEvent, ActionClass } from "../../lib/types";
import { AmbiguityResolver } from "../../features/voice/AmbiguityResolver";

const CLASS_BADGE: Record<string, string> = {
  AUTO:         "bg-green-900/70 text-green-300",
  CONFIRM:      "bg-amber-900/70 text-amber-300",
  DENY:         "bg-red-900/70 text-red-300",
  ERROR:        "bg-red-900/70 text-red-400",
  INFO:         "bg-sky-900/70 text-sky-300",
  WARN:         "bg-yellow-900/70 text-yellow-300",
  DETERMINISTIC:"bg-blue-900/70 text-blue-300",
  STRUCTURED:   "bg-purple-900/70 text-purple-300",
  CREATIVE:     "bg-pink-900/70 text-pink-300",
  UNKNOWN:      "bg-slate-800 text-slate-400",
};

const TYPE_CONFIG: Record<string, { icon: string; border: string; bg: string; label: string }> = {
  intent:    { icon: "💬", border: "border-blue-800/60",   bg: "bg-blue-950/30",  label: "Намір" },
  tool_call: { icon: "⚙️",  border: "border-amber-800/60", bg: "bg-amber-950/30", label: "Виклик" },
  result:    { icon: "✓",  border: "border-slate-700",    bg: "bg-slate-800/40", label: "Результат" },
};

const RESULT_BORDER: Record<string, string> = {
  AUTO:    "border-green-700/60 bg-green-950/30",
  CONFIRM: "border-amber-700/60 bg-amber-950/30",
  DENY:    "border-red-700/60 bg-red-950/30",
  ERROR:   "border-red-700/60 bg-red-950/30",
  WARN:    "border-yellow-700/60 bg-yellow-950/30",
  INFO:    "border-sky-700/60 bg-sky-950/30",
};

const RESULT_LABEL: Record<string, string> = {
  AUTO:    "✅ Виконано",
  CONFIRM: "⏳ Підтвердження",
  DENY:    "🚫 Заблоковано",
  ERROR:   "❌ Помилка",
  WARN:    "⚠️ Попередження",
  INFO:    "ℹ️ Відповідь",
};

const FAILURE_LABEL: Record<string, string> = {
  ambiguous:           "🔀 Оберіть пристрій",
  device_not_found:    "🔍 Пристрій не знайдено",
  unclear_intent:      "🤔 Незрозуміла команда",
  unknown_device_kind: "❓ Невідомий тип",
  unsupported_action:  "⛔ Дія не підтримується",
};

const TOOL_LABEL: Record<string, string> = {
  mqtt_publish:     "Пристрій",
  ask_user:         "Уточнення",
  set_timer:        "Таймер",
  send_push:        "Сповіщення",
  get_home_state:   "Стан дому",
  query_events_db:  "База подій",
  summarize_period: "Підсумок",
};

function EventCard({ ev }: { ev: AgentTurnEvent }) {
  const [expanded, setExpanded] = useState(false);
  const [reasonExpanded, setReasonExpanded] = useState(false);

  const isResult = ev.type === "result";
  const ac = ev.action_class as ActionClass | undefined;
  const borderBg = isResult && ac ? (RESULT_BORDER[ac] ?? "border-slate-700 bg-slate-800/40") : undefined;
  const cfg = TYPE_CONFIG[ev.type] ?? { icon: "•", border: "border-slate-700", bg: "bg-slate-800/60", label: ev.type };

  const badge = !isResult ? (ev.class_ ?? undefined) : undefined;
  const hasPayload = ev.payload != null && typeof ev.payload === "object" && Object.keys(ev.payload as object).length > 0;
  const hasReasoning = isResult && !!ev.reasoning;
  const isAmbiguous = isResult && ev.failure_kind === "ambiguous" && (ev.candidates?.length ?? 0) > 0;

  const resultLabel = isResult && ac
    ? (ev.failure_kind ? (FAILURE_LABEL[ev.failure_kind] ?? RESULT_LABEL[ac] ?? "Результат") : (RESULT_LABEL[ac] ?? "Результат"))
    : cfg.label;

  const toolDisplay = ev.tool ? (TOOL_LABEL[ev.tool] ?? ev.tool) : null;

  return (
    <div className={`rounded-lg border px-3 py-2 text-xs ${borderBg ?? `${cfg.border} ${cfg.bg}`}`}>
      <div className="flex items-start gap-2">
        <span className="text-base leading-none mt-0.5 select-none">{cfg.icon}</span>
        <div className="flex-1 min-w-0 space-y-0.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-semibold text-white/80">{resultLabel}</span>
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
               style={{ display: "-webkit-box", WebkitLineClamp: expanded || isAmbiguous ? undefined : 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
              {ev.text}
            </p>
          )}
          {toolDisplay && (
            <p className="font-mono text-amber-300/80">{toolDisplay}{ev.topic ? ` → ${ev.topic}` : ""}</p>
          )}

          {/* Ambiguity resolver — inline candidate picker */}
          {isAmbiguous && ev.text && (
            <AmbiguityResolver intentText={ev.text} candidates={ev.candidates!} />
          )}

          {/* Reasoning fold */}
          {hasReasoning && (
            <button
              onClick={() => setReasonExpanded((v) => !v)}
              className="text-slate-500 hover:text-slate-300 mt-0.5"
            >
              {reasonExpanded ? "▲ скрити причину" : "▼ Чому?"}
            </button>
          )}
          {reasonExpanded && hasReasoning && (
            <p className="mt-1 rounded bg-black/30 p-2 text-[10px] text-slate-300 leading-relaxed">
              {ev.reasoning}
            </p>
          )}

          {/* Payload fold */}
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

export default function AgentStreamTab() {
  const [events, setEvents] = useState<AgentTurnEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const seenTs = useRef<Set<string>>(new Set());

  function appendEvent(ev: AgentTurnEvent) {
    const key = ev.ts ?? "";
    if (key && seenTs.current.has(key)) return;
    if (key) seenTs.current.add(key);
    setEvents((prev) => [...prev.slice(-199), ev]);
  }

  function loadHistory() {
    api.get<AgentTurnEvent[]>("/api/agent/history")
      .then((history) => {
        history.forEach((ev) => {
          const key = ev.ts ?? "";
          if (key) seenTs.current.add(key);
        });
        setEvents(history);
        requestAnimationFrame(() => {
          listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
        });
      })
      .catch(() => { });
  }

  // Hydrate with recent history on mount
  useEffect(() => { loadHistory(); }, []);

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
          appendEvent(ev);
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
        <div className="flex items-center gap-3">
          <button onClick={loadHistory} className="text-xs text-slate-500 hover:text-slate-300">
            ↺ Оновити
          </button>
          {events.length > 0 && (
            <button onClick={() => setEvents([])} className="text-xs text-slate-500 hover:text-slate-300">
              Очистити
            </button>
          )}
        </div>
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
