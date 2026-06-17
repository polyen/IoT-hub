import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Play, Clock } from "lucide-react";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { relativeTime } from "../../lib/format";
import type { AgentAuditEntry } from "../../lib/types";
import { SCENES, type Scene } from "../../features/scenes/scenes";
import { ACTION_COLORS } from "./shared";

function ScenarioCard({
  scenario,
  lastRun,
  running,
  onRun,
}: {
  scenario: Scene;
  lastRun?: AgentAuditEntry;
  running: boolean;
  onRun: () => void;
}) {
  return (
    <div
      className="rounded-xl border border-[color:var(--border)] bg-[color:var(--card)] hover:bg-[color:var(--card-hover)] p-4 transition-colors flex items-center gap-4"
    >
      <span className="text-3xl select-none">{scenario.icon}</span>
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-sm text-[color:var(--text)]">{scenario.name}</p>
        <p className="text-xs text-[color:var(--text-muted)] mt-0.5 truncate">{scenario.description}</p>
        {lastRun && (
          <p className="text-xs text-[color:var(--text-faint)] mt-1 flex items-center gap-1">
            <Clock size={10} />
            {relativeTime(lastRun.timestamp)}
          </p>
        )}
      </div>
      <button
        onClick={onRun}
        disabled={running}
        className="shrink-0 flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 disabled:opacity-40 px-3 py-2 text-xs font-medium text-white transition-colors"
      >
        {running ? <Spinner className="h-3 w-3" /> : <Play size={13} />}
        {running ? "..." : "Запуск"}
      </button>
    </div>
  );
}

// ── Policy simulator (dry-run; does not execute) ────────────────────────────

interface TryResult {
  matched_rule: string;
  action_class: string;
  reason: string;
  latency_ms: number;
  inferred_tool?: string | null;
}

function PolicySimulator() {
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
    <div className="rounded-xl border border-[color:var(--border)] bg-[color:var(--card)] p-4 space-y-3">
      <p className="text-xs text-[color:var(--text-muted)]">
        Перевір, як намір буде класифіковано політикою безпеки — без виконання.
      </p>
      <form onSubmit={handleSubmit} className="space-y-3">
        <textarea
          value={intentText}
          onChange={(e) => setIntentText(e.target.value)}
          rows={2}
          placeholder="напр. «Вимкни всі лампи в будинку»"
          className="w-full rounded-lg border border-[color:var(--border)] bg-[color:var(--card)] px-3 py-2 text-sm text-[color:var(--text)] placeholder-[color:var(--text-faint)] focus:outline-none focus:ring-2 focus:ring-primary-500 resize-none"
        />
        <input
          value={tool}
          onChange={(e) => setTool(e.target.value)}
          placeholder="Інструмент (необов'язково), напр. mqtt_publish"
          className="w-full rounded-lg border border-[color:var(--border)] bg-[color:var(--card)] px-3 py-2 text-sm text-[color:var(--text)] placeholder-[color:var(--text-faint)] focus:outline-none focus:ring-2 focus:ring-primary-500"
        />
        <Button
          type="submit"
          variant="secondary"
          size="sm"
          disabled={tryMutation.isPending || !intentText.trim()}
        >
          {tryMutation.isPending ? "Перевіряємо…" : "Симулювати"}
        </Button>
      </form>

      {result && (
        <div className="rounded-lg border border-[color:var(--border)] bg-[color:var(--bg-secondary)] p-4 space-y-2 text-sm">
          <div className="flex items-center gap-3">
            <span className={`rounded px-2 py-0.5 text-xs font-bold ${ACTION_COLORS[result.action_class] ?? "bg-[color:var(--card-hover)] text-[color:var(--text-muted)]"}`}>
              {result.action_class}
            </span>
            <span className="text-[color:var(--text-muted)]">{result.latency_ms} мс</span>
            {result.inferred_tool && (
              <span className="rounded bg-[color:var(--card-hover)] px-2 py-0.5 text-xs text-[color:var(--text-muted)]">
                ↳ <span className="font-mono">{result.inferred_tool}</span> (інференс)
              </span>
            )}
          </div>
          <p className="text-[color:var(--text-muted)]"><span className="text-[color:var(--text-faint)]">Правило: </span>{result.matched_rule}</p>
          <p className="text-[color:var(--text-muted)] text-xs">{result.reason}</p>
        </div>
      )}
    </div>
  );
}

export default function ScenariosTab() {
  const [runningId, setRunningId] = useState<string | null>(null);
  const [customIntent, setCustomIntent] = useState("");

  const { data: auditLog } = useQuery<AgentAuditEntry[]>({
    queryKey: ["agent-audit", 50],
    queryFn: () => api.get<AgentAuditEntry[]>("/api/agent/audit?limit=50"),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  const runMutation = useMutation({
    mutationFn: (intent_text: string) =>
      api.post<{ result: string; id: string }>("/api/agent/run", { intent_text }),
    onSuccess: () => {
      toast.success("Сценарій запущено — агент обробляє команду");
      setRunningId(null);
      setCustomIntent("");
    },
    onError: () => {
      toast.error("Не вдалося запустити сценарій");
      setRunningId(null);
    },
  });

  function handleRun(scenario: Scene) {
    setRunningId(scenario.id);
    runMutation.mutate(scenario.intent);
  }

  function handleCustomRun() {
    if (!customIntent.trim()) return;
    setRunningId("custom");
    runMutation.mutate(customIntent.trim());
  }

  // Find last run for each scenario by matching intent prefix in audit log
  function findLastRun(scenario: Scene): AgentAuditEntry | undefined {
    return auditLog?.find((a) =>
      a.intent_text.toLowerCase().includes(scenario.intent.split(" ")[0].toLowerCase())
    );
  }

  return (
    <div className="space-y-6">
      {/* Scenario cards */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[color:var(--text-muted)]">
          Готові сценарії
        </h2>
        <div className="space-y-2">
          {SCENES.map((scenario) => (
            <ScenarioCard
              key={scenario.id}
              scenario={scenario}
              lastRun={findLastRun(scenario)}
              running={runningId === scenario.id}
              onRun={() => handleRun(scenario)}
            />
          ))}
        </div>
      </section>

      {/* Custom intent */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[color:var(--text-muted)]">
          Довільна команда
        </h2>
        <div className="rounded-xl border border-[color:var(--border)] bg-[color:var(--card)] p-4 space-y-3">
          <p className="text-xs text-[color:var(--text-muted)]">
            Введи команду природною мовою — агент інтерпретує та виконає через policy
          </p>
          <textarea
            value={customIntent}
            onChange={(e) => setCustomIntent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleCustomRun();
            }}
            rows={2}
            placeholder="напр. увімкни лампу в спальні та закрий замок на вході…"
            className="w-full rounded-lg border border-[color:var(--border)] bg-[color:var(--card)] px-3 py-2 text-sm text-[color:var(--text)] placeholder-[color:var(--text-faint)] resize-none focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <div className="flex items-center justify-between">
            <p className="text-xs text-[color:var(--text-faint)]">Ctrl+Enter для надсилання</p>
            <Button
              variant="primary"
              size="sm"
              disabled={!customIntent.trim() || runningId === "custom"}
              onClick={handleCustomRun}
              className="gap-1.5"
            >
              {runningId === "custom" ? <Spinner className="h-3 w-3" /> : <Play size={13} />}
              Виконати
            </Button>
          </div>
        </div>
      </section>

      {/* Policy simulator (dry-run) */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[color:var(--text-muted)]">
          Симулятор політики
        </h2>
        <PolicySimulator />
      </section>
    </div>
  );
}
