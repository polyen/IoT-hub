import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Play, Clock, Zap } from "lucide-react";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { relativeTime } from "../../lib/format";
import type { AgentAuditEntry } from "../../lib/types";

interface Scenario {
  id: string;
  icon: string;
  name: string;
  description: string;
  intent: string;
  color: string;
}

const SCENARIOS: Scenario[] = [
  {
    id: "morning",
    icon: "☀️",
    name: "Ранок",
    description: "Увімкни світло, вимкни охорону",
    intent: "увімкни світло у всіх кімнатах і вимкни охорону",
    color: "border-amber-800/60 bg-amber-950/20 hover:bg-amber-950/40",
  },
  {
    id: "evening",
    icon: "🌆",
    name: "Вечір",
    description: "Приглуши світло до 30%",
    intent: "приглуши яскравість світла до 30 відсотків у вітальні",
    color: "border-blue-800/60 bg-blue-950/20 hover:bg-blue-950/40",
  },
  {
    id: "night",
    icon: "🌙",
    name: "Нічний режим",
    description: "Вимкни світло, охорона дому",
    intent: "вимкни все світло і увімкни охорону дому",
    color: "border-indigo-800/60 bg-indigo-950/20 hover:bg-indigo-950/40",
  },
  {
    id: "leaving",
    icon: "🚪",
    name: "Покидаю дім",
    description: "Вимкни все, охорона від",
    intent: "вимкни всі пристрої і увімкни охорону відсутній",
    color: "border-red-800/60 bg-red-950/20 hover:bg-red-950/40",
  },
  {
    id: "returning",
    icon: "🏠",
    name: "Повертаюсь додому",
    description: "Вимкни охорону, увімкни світло",
    intent: "вимкни охорону і увімкни освітлення у передпокої",
    color: "border-green-800/60 bg-green-950/20 hover:bg-green-950/40",
  },
  {
    id: "movie",
    icon: "🎬",
    name: "Кіно",
    description: "Мінімальне світло у вітальні",
    intent: "вимкни яскраве світло, залиш лише підсвічування у вітальні",
    color: "border-purple-800/60 bg-purple-950/20 hover:bg-purple-950/40",
  },
];

function ScenarioCard({
  scenario,
  lastRun,
  running,
  onRun,
}: {
  scenario: Scenario;
  lastRun?: AgentAuditEntry;
  running: boolean;
  onRun: () => void;
}) {
  return (
    <div
      className={`rounded-xl border p-4 transition-colors ${scenario.color} flex items-center gap-4`}
    >
      <span className="text-3xl select-none">{scenario.icon}</span>
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-sm text-white">{scenario.name}</p>
        <p className="text-xs text-slate-400 mt-0.5 truncate">{scenario.description}</p>
        {lastRun && (
          <p className="text-xs text-slate-600 mt-1 flex items-center gap-1">
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

export default function ScenariosPage() {
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

  function handleRun(scenario: Scenario) {
    setRunningId(scenario.id);
    runMutation.mutate(scenario.intent);
  }

  function handleCustomRun() {
    if (!customIntent.trim()) return;
    setRunningId("custom");
    runMutation.mutate(customIntent.trim());
  }

  // Find last run for each scenario by matching intent prefix in audit log
  function findLastRun(scenario: Scenario): AgentAuditEntry | undefined {
    return auditLog?.find((a) =>
      a.intent_text.toLowerCase().includes(scenario.intent.split(" ")[0].toLowerCase())
    );
  }

  const recentRuns = auditLog?.slice(0, 10) ?? [];

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center gap-2">
        <Zap size={20} className="text-primary-400" />
        <h1 className="text-xl font-semibold">Сценарії</h1>
      </div>

      {/* Scenario cards */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Готові сценарії
        </h2>
        <div className="space-y-2">
          {SCENARIOS.map((scenario) => (
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
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Довільна команда
        </h2>
        <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-4 space-y-3">
          <p className="text-xs text-slate-400">
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
            className="w-full rounded-lg border border-slate-600 bg-slate-700/60 px-3 py-2 text-sm text-white placeholder-slate-500 resize-none focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <div className="flex items-center justify-between">
            <p className="text-xs text-slate-600">Ctrl+Enter для надсилання</p>
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

      {/* Recent runs */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Останні дії агента
        </h2>
        {recentRuns.length === 0 ? (
          <div className="rounded-xl border border-slate-700 bg-slate-800/40 px-4 py-6 text-center text-xs text-slate-500">
            Ще не було дій
          </div>
        ) : (
          <div className="rounded-xl border border-slate-700 bg-slate-800/60 divide-y divide-slate-700">
            {recentRuns.map((run) => (
              <div key={run.id} className="flex items-start gap-3 px-4 py-2.5 text-sm">
                <span
                  className={`mt-1 h-2 w-2 rounded-full shrink-0 ${
                    run.action_class === "DENY"
                      ? "bg-red-500"
                      : run.action_class === "CONFIRM"
                      ? "bg-amber-500"
                      : "bg-green-500"
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-slate-300 truncate text-sm">{run.intent_text}</p>
                  {run.tool && (
                    <p className="text-xs text-slate-500 font-mono mt-0.5">{run.tool}</p>
                  )}
                </div>
                <span className="text-xs text-slate-500 shrink-0 mt-0.5">
                  {relativeTime(run.timestamp)}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
