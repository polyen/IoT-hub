import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";

export interface Scene {
  id: string;
  icon: string;
  /** Full label used on cards. */
  name: string;
  /** Compact label used on Home chips (falls back to `name`). */
  short?: string;
  description: string;
  /** Natural-language intent dispatched to the agent (passes through policy). */
  intent: string;
  // Identity is conveyed by `icon`, not colour — colour is reserved for state (idle/presence/alert).
  /** Surfaced as a quick chip on the Home screen. */
  quick?: boolean;
}

/**
 * Single source of truth for scenes — consumed by:
 *  - Home `SceneChips` (subset where `quick`),
 *  - `/scenes` `ScenesPage` (full grid),
 *  - Assistant `ScenariosTab` (full grid + audit + simulator).
 */
export const SCENES: Scene[] = [
  {
    id: "leaving",
    icon: "🚪",
    name: "Покидаю дім",
    short: "Я пішов",
    description: "Вимкни все, охорона «відсутній»",
    intent: "вимкни всі пристрої і увімкни охорону відсутній",
    quick: true,
  },
  {
    id: "night",
    icon: "🌙",
    name: "Нічний режим",
    short: "Ніч",
    description: "Вимкни світло, охорона дому",
    intent: "вимкни все світло і увімкни охорону дому",
    quick: true,
  },
  {
    id: "morning",
    icon: "☀️",
    name: "Ранок",
    description: "Увімкни світло, вимкни охорону",
    intent: "увімкни світло у всіх кімнатах і вимкни охорону",
    quick: true,
  },
  {
    id: "movie",
    icon: "🎬",
    name: "Кіно",
    description: "Мінімальне світло у вітальні",
    intent: "вимкни яскраве світло, залиш лише підсвічування у вітальні",
    quick: true,
  },
  {
    id: "evening",
    icon: "🌆",
    name: "Вечір",
    description: "Приглуши світло до 30%",
    intent: "приглуши яскравість світла до 30 відсотків у вітальні",
  },
  {
    id: "returning",
    icon: "🏠",
    name: "Повертаюсь додому",
    description: "Вимкни охорону, увімкни світло",
    intent: "вимкни охорону і увімкни освітлення у передпокої",
  },
];

/** Dispatches a scene intent to the agent and tracks which one is running. */
export function useRunScene() {
  const [runningId, setRunningId] = useState<string | null>(null);

  const mutation = useMutation({
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

  function run(scene: Scene) {
    setRunningId(scene.id);
    mutation.mutate(scene.intent);
  }

  return { run, runningId };
}
