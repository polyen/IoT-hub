import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Bot } from "lucide-react";
import ConversationTab from "./ConversationTab";
import ScenariosTab from "./ScenariosTab";
import AgentStreamTab from "./AgentStreamTab";
import AuditTab from "./AuditTab";

type Tab = "conversation" | "scenarios" | "stream" | "audit";

const TABS: { id: Tab; label: string }[] = [
  { id: "conversation", label: "Розмова" },
  { id: "scenarios", label: "Сценарії" },
  { id: "stream", label: "Потік" },
  { id: "audit", label: "Журнал" },
];

const VALID: Tab[] = ["conversation", "scenarios", "stream", "audit"];

export default function AssistantPage() {
  const [params, setParams] = useSearchParams();
  const initial = params.get("tab");
  const [tab, setTab] = useState<Tab>(
    VALID.includes(initial as Tab) ? (initial as Tab) : "conversation",
  );

  function select(id: Tab) {
    setTab(id);
    setParams(id === "conversation" ? {} : { tab: id }, { replace: true });
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <Bot size={20} className="text-primary-400" />
        <h1 className="text-xl font-semibold">Асистент</h1>
      </div>

      <div className="flex gap-0.5 rounded-lg border border-slate-700 bg-slate-800/60 p-1 w-fit overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => select(t.id)}
            className={[
              "rounded px-4 py-1.5 text-sm font-medium transition-colors whitespace-nowrap",
              tab === t.id ? "bg-slate-700 text-white" : "text-slate-400 hover:text-white",
            ].join(" ")}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "conversation" && <ConversationTab />}
      {tab === "scenarios" && <ScenariosTab />}
      {tab === "stream" && <AgentStreamTab />}
      {tab === "audit" && <AuditTab />}
    </div>
  );
}
