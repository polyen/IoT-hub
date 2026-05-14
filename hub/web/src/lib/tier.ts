export const TIER_COLORS: Record<number, string> = {
  0: "#ef4444",
  1: "#f59e0b",
  2: "#3b82f6",
  3: "#64748b",
};

export const TIER_BG_CLASSES: Record<number, string> = {
  0: "bg-red-950 text-red-300 border-red-800",
  1: "bg-amber-950 text-amber-300 border-amber-800",
  2: "bg-blue-950 text-blue-300 border-blue-800",
  3: "bg-slate-800 text-slate-400 border-slate-700",
};

export const TIER_LABELS: Record<number, { uk: string; en: string }> = {
  0: { uk: "Публічний агрегат", en: "Public aggregate" },
  1: { uk: "Несенситивний", en: "Non-sensitive" },
  2: { uk: "Чутливий", en: "Sensitive" },
  3: { uk: "Приватний", en: "Private" },
};

export function tierBgClass(tier: number): string {
  return TIER_BG_CLASSES[tier] ?? TIER_BG_CLASSES[3];
}

export function tierColor(tier: number): string {
  return TIER_COLORS[tier] ?? TIER_COLORS[3];
}
