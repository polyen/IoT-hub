import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { Spinner } from "../../components/Spinner";

interface Scene {
  id: string;
  icon: string;
  name: string;
  /** Natural-language intent dispatched to the agent (passes through policy). */
  intent: string;
}

// Compact set surfaced on Home — the full library lives on /assistant?tab=scenarios.
const SCENES: Scene[] = [
  { id: "leaving", icon: "🚪", name: "Я пішов", intent: "вимкни всі пристрої і увімкни охорону відсутній" },
  { id: "night", icon: "🌙", name: "Ніч", intent: "вимкни все світло і увімкни охорону дому" },
  { id: "morning", icon: "☀️", name: "Ранок", intent: "увімкни світло у всіх кімнатах і вимкни охорону" },
  { id: "movie", icon: "🎬", name: "Кіно", intent: "вимкни яскраве світло, залиш лише підсвічування у вітальні" },
];

export function SceneChips() {
  const [runningId, setRunningId] = useState<string | null>(null);

  const run = useMutation({
    mutationFn: (intent_text: string) =>
      api.post<{ result: string; id: string }>("/api/agent/run", { intent_text }, true),
    onSuccess: () => {
      toast.success("Сценарій запущено");
      setRunningId(null);
    },
    onError: () => {
      toast.error("Не вдалося запустити сценарій");
      setRunningId(null);
    },
  });

  return (
    <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none]">
      {SCENES.map((scene) => {
        const busy = runningId === scene.id;
        return (
          <button
            key={scene.id}
            disabled={busy}
            onClick={() => {
              setRunningId(scene.id);
              run.mutate(scene.intent);
            }}
            className="card card-hover flex shrink-0 items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium text-[color:var(--text)] disabled:opacity-50"
          >
            {busy ? <Spinner className="h-4 w-4" /> : <span className="text-lg leading-none">{scene.icon}</span>}
            {scene.name}
          </button>
        );
      })}
    </div>
  );
}
