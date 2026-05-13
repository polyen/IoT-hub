import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";

interface LintIssue {
  level: "error" | "warning";
  message: string;
}

interface SimResult {
  matched_rule: string;
  class: string;
  reason: string;
  overrides: unknown[];
}

const ACTION_COLORS: Record<string, string> = {
  AUTO: "bg-green-900/60 text-green-300 border-green-800",
  CONFIRM: "bg-amber-900/60 text-amber-300 border-amber-800",
  DENY: "bg-red-900/60 text-red-300 border-red-800",
};

export default function PolicyPage() {
  const [simIntent, setSimIntent] = useState("");
  const [simTool, setSimTool] = useState("");
  const [simResult, setSimResult] = useState<SimResult | null>(null);

  const { data: policy, isLoading: policyLoading } = useQuery<Record<string, unknown>>({
    queryKey: ["policy"],
    queryFn: () => api.get("/api/policy"),
    staleTime: 300_000,
  });

  const { data: lint, isLoading: lintLoading } = useQuery<LintIssue[]>({
    queryKey: ["policy-lint"],
    queryFn: () => api.get("/api/policy/lint"),
    staleTime: 300_000,
  });

  const simMutation = useMutation({
    mutationFn: (body: { intent_text: string; tool?: string }) =>
      api.post<SimResult>("/api/policy/simulate", body),
    onSuccess: (data) => setSimResult(data),
  });

  const isLoading = policyLoading || lintLoading;

  if (isLoading) return <div className="flex justify-center pt-16"><Spinner className="h-8 w-8" /></div>;

  const errors = (lint ?? []).filter((i) => i.level === "error");
  const warnings = (lint ?? []).filter((i) => i.level === "warning");

  // Extract typed values from unknown policy object
  const defaultClass = policy != null && typeof policy.default === "string" ? policy.default : null;
  const toolsMap: Record<string, { class: string }> | null =
    policy != null && policy.tools != null && typeof policy.tools === "object"
      ? (policy.tools as Record<string, { class: string }>)
      : null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Політика безпеки</h1>
        <span className="text-xs text-slate-500">Тільки перегляд</span>
      </div>

      {/* Lint results */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Перевірка</h2>
        {lint && lint.length === 0 ? (
          <div className="flex items-center gap-2 rounded-lg border border-green-800 bg-green-900/30 px-4 py-3 text-sm text-green-300">
            <span>✓</span> Помилок не знайдено
          </div>
        ) : (
          <div className="space-y-1.5">
            {errors.map((issue, i) => (
              <div key={i} className="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2.5 text-sm">
                <span className="font-semibold text-red-400">Помилка: </span>
                <span className="text-red-200">{issue.message}</span>
              </div>
            ))}
            {warnings.map((issue, i) => (
              <div key={i} className="rounded-lg border border-amber-800 bg-amber-900/30 px-4 py-2.5 text-sm">
                <span className="font-semibold text-amber-400">Попередження: </span>
                <span className="text-amber-200">{issue.message}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Simulator */}
      <section className="space-y-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Симулятор</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!simIntent.trim()) return;
            simMutation.mutate({ intent_text: simIntent, tool: simTool || undefined });
          }}
          className="space-y-3"
        >
          <label className="block space-y-1">
            <span className="text-xs text-slate-400">Намір</span>
            <input
              value={simIntent}
              onChange={(e) => setSimIntent(e.target.value)}
              placeholder="напр. «Відкрий ворота»"
              className="w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-slate-400">Інструмент (необов'язково)</span>
            <input
              value={simTool}
              onChange={(e) => setSimTool(e.target.value)}
              placeholder="mqtt_publish"
              className="w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white placeholder-slate-500 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <Button type="submit" size="sm" variant="primary" disabled={simMutation.isPending || !simIntent.trim()}>
            {simMutation.isPending ? "Перевіряємо…" : "Симулювати"}
          </Button>
        </form>

        {simResult && (
          <div className={`rounded-lg border px-4 py-3 space-y-1.5 text-sm ${ACTION_COLORS[simResult.class] ?? "border-slate-700 bg-slate-800"}`}>
            <div className="flex items-center gap-2">
              <span className="font-bold">{simResult.class}</span>
              <span className="text-xs opacity-70">·</span>
              <span className="text-xs opacity-70">{simResult.matched_rule}</span>
            </div>
            <p className="text-xs opacity-80">{simResult.reason}</p>
          </div>
        )}
      </section>

      {/* Policy viewer */}
      {policy != null && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            policy.yaml
          </h2>

          {/* Default class */}
          {defaultClass != null && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-slate-400">Клас за замовчуванням:</span>
              <span className={`rounded px-2 py-0.5 text-xs font-bold ${ACTION_COLORS[defaultClass] ?? "bg-slate-700"}`}>
                {defaultClass}
              </span>
            </div>
          )}

          {/* Tools table */}
          {toolsMap != null && Object.keys(toolsMap).length > 0 && (
            <div className="space-y-1">
              <p className="text-xs text-slate-500">Інструменти ({Object.keys(toolsMap).length})</p>
              <div className="rounded-lg border border-slate-700 overflow-hidden divide-y divide-slate-700">
                {Object.entries(toolsMap).map(([name, cfg]) => (
                  <div key={name} className="flex items-center justify-between px-4 py-2.5 text-sm">
                    <span className="font-mono text-slate-300">{name}</span>
                    <span className={`rounded px-2 py-0.5 text-xs font-bold ${ACTION_COLORS[cfg.class] ?? "bg-slate-700 text-slate-300"}`}>
                      {cfg.class}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Raw JSON */}
          <details className="group">
            <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300 select-none py-1">
              Показати повний JSON
            </summary>
            <pre className="mt-2 rounded-lg border border-slate-700 bg-slate-900 p-4 text-xs text-slate-300 overflow-auto max-h-96 font-mono leading-relaxed">
              {JSON.stringify(policy, null, 2)}
            </pre>
          </details>
        </section>
      )}
    </div>
  );
}
