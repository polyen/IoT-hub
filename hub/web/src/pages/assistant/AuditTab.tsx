import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Spinner } from "../../components/Spinner";
import { shortDateTime } from "../../lib/format";
import type { AgentAuditEntry } from "../../lib/types";
import { ACTION_COLORS } from "./shared";

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

export default function AuditTab() {
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
