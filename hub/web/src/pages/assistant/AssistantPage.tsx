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
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 bg-primary-500/15">
          <Bot size={18} className="text-primary-400" />
        </div>
        <div>
          <h1 className="text-xl font-semibold leading-none">Асистент</h1>
          <p className="text-xs text-[color:var(--text-muted)] mt-1">
            Голос, сценарії та хід обробки агента
          </p>
        </div>
      </div>

      <div className="flex gap-0.5 rounded-xl border border-[color:var(--border)] bg-[color:var(--card)] p-1 w-fit overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => select(t.id)}
            className={[
              "rounded-lg px-4 py-1.5 text-sm font-medium transition-all whitespace-nowrap",
              tab === t.id
                ? "bg-primary-600 text-white shadow-sm"
                : "text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)]",
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
