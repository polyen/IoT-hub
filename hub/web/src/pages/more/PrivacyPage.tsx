import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import type { PrivacyReport } from "../../lib/types";

const TIER_LABELS: Record<string, string> = {
  "0": "T0 — сирі кадри / аудіо / вектори (тільки edge)",
  "1": "T1 — персональні події (виявлення, ідентифікація)",
  "2": "T2 — агреговані дані (хмара з consent)",
  "3": "T3 — операційні метрики (публічні)",
};

const TIER_COLOR: Record<string, string> = {
  "0": "bg-red-500",
  "1": "bg-amber-500",
  "2": "bg-blue-500",
  "3": "bg-slate-500",
};

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(2)} MB`;
}

function WipeDialog({ onClose }: { onClose: () => void }) {
  const [tiers, setTiers] = useState<number[]>([2, 3]);
  const [since, setSince] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  });
  const [until, setUntil] = useState(() => new Date().toISOString().slice(0, 10));

  const mutation = useMutation({
    mutationFn: () => api.post("/api/privacy/wipe", { tiers, since: `${since}T00:00:00Z`, until: `${until}T23:59:59Z` }),
    onSuccess: () => {
      toast.success("Запит на видалення надіслано — потрібне підтвердження");
      onClose();
    },
  });

  function toggleTier(t: number) {
    setTiers((prev) => prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-md space-y-5 rounded-xl border border-slate-700 bg-slate-800 p-6 shadow-2xl">
        <h3 className="font-semibold text-white">Видалення даних (DSAR)</h3>
        <p className="text-xs text-slate-400">
          Після підтвердження всі дані обраних рівнів будуть безповоротно видалені.
        </p>

        <div className="space-y-2">
          <p className="text-xs text-slate-400">Рівні для видалення</p>
          {[2, 3].map((t) => (
            <label key={t} className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={tiers.includes(t)}
                onChange={() => toggleTier(t)}
                className="h-4 w-4 rounded border-slate-600 bg-slate-700 text-blue-500"
              />
              <span className="text-sm text-slate-300">{TIER_LABELS[String(t)]}</span>
            </label>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block space-y-1">
            <span className="text-xs text-slate-400">Від</span>
            <input type="date" value={since} onChange={(e) => setSince(e.target.value)}
              className="w-full rounded border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-slate-400">До</span>
            <input type="date" value={until} onChange={(e) => setUntil(e.target.value)}
              className="w-full rounded border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </label>
        </div>

        <div className="flex justify-end gap-2">
          <Button size="sm" variant="secondary" onClick={onClose}>Скасувати</Button>
          <Button
            size="sm"
            variant="primary"
            disabled={tiers.length === 0 || mutation.isPending}
            onClick={() => mutation.mutate()}
            className="bg-red-700 hover:bg-red-600"
          >
            {mutation.isPending ? "Надсилаємо…" : "Надіслати запит"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function PrivacyPage() {
  const qc = useQueryClient();
  const [wipeOpen, setWipeOpen] = useState(false);

  const { data, isLoading } = useQuery<PrivacyReport>({
    queryKey: ["privacy-report"],
    queryFn: () => api.get<PrivacyReport>("/api/privacy/report"),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  const consentMutation = useMutation({
    mutationFn: (enabled: boolean) => api.post("/api/privacy/cloud_consent", { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["privacy-report"] }),
    onError: () => toast.error("Не вдалося змінити налаштування"),
  });

  if (isLoading) return <div className="flex justify-center pt-16"><Spinner className="h-8 w-8" /></div>;

  const totalBytes = data?.sent_to_cloud_bytes_7d ?? 0;
  const byTier = data?.by_tier ?? {};
  const byTool = data?.by_tool ?? {};
  const cloudConsent = data?.cloud_consent_state ?? true;

  const tierEntries = Object.entries(byTier).sort(([a], [b]) => Number(a) - Number(b));
  const toolEntries = Object.entries(byTool).sort(([, a], [, b]) => b - a);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Приватність</h1>

      {/* Cloud consent toggle */}
      <section className="rounded-lg border border-slate-700 bg-slate-800/60 p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium">Хмарний fallback</p>
            <p className="text-xs text-slate-400 mt-0.5">
              Дозволяє надсилати T2/T3 агрегати на хмарний LLM при недоступності локальної моделі.
              T0/T1 дані ніколи не покидають LAN.
            </p>
          </div>
          <button
            onClick={() => consentMutation.mutate(!cloudConsent)}
            disabled={consentMutation.isPending}
            className={`relative h-6 w-11 rounded-full transition-colors ${
              consentMutation.isPending
                ? "opacity-50 cursor-not-allowed bg-slate-500"
                : cloudConsent
                ? "bg-blue-600"
                : "bg-slate-600"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
                consentMutation.isPending
                  ? "animate-pulse"
                  : cloudConsent
                  ? "translate-x-5"
                  : "translate-x-0"
              }`}
            />
          </button>
        </div>
        {cloudConsent && (
          <p className="mt-3 text-xs text-amber-400 flex items-center gap-1.5">
            ⚠️ T2/T3 агрегати можуть бути відправлені на зовнішній сервер
          </p>
        )}
      </section>

      {/* Cloud bytes report */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Відправлено в хмару (7 днів)
          </h2>
          <span className="text-sm font-medium">{formatBytes(totalBytes)}</span>
        </div>

        {tierEntries.length > 0 ? (
          <div className="rounded-lg border border-slate-700 bg-slate-800/60 divide-y divide-slate-700">
            {tierEntries.map(([tier, bytes]) => (
              <div key={tier} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <span className={`h-2 w-2 rounded-full shrink-0 ${TIER_COLOR[tier] ?? "bg-slate-500"}`} />
                <span className="flex-1 text-slate-300">{TIER_LABELS[tier] ?? `Tier ${tier}`}</span>
                <span className="text-slate-400 font-mono text-xs">{formatBytes(bytes)}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-slate-700 bg-slate-800/40 px-4 py-3 text-xs text-slate-500">
            Даних немає — або нічого не відправлялось, або Redis ще не зібрав статистику
          </div>
        )}
      </section>

      {/* By tool */}
      {toolEntries.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">За інструментом</h2>
          <div className="rounded-lg border border-slate-700 bg-slate-800/60 divide-y divide-slate-700">
            {toolEntries.map(([tool, bytes]) => (
              <div key={tool} className="flex items-center justify-between px-4 py-2.5 text-sm">
                <span className="font-mono text-slate-300 text-xs">{tool}</span>
                <span className="text-slate-400 text-xs">{formatBytes(bytes)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* DSAR wipe */}
      <section className="rounded-lg border border-red-900/50 bg-red-950/20 p-4 space-y-3">
        <h2 className="text-sm font-semibold text-red-300">Видалення даних</h2>
        <p className="text-xs text-slate-400">
          Видалити T2/T3 дані за вказаний період. Потребує окремого підтвердження через Confirm-flow.
        </p>
        <Button size="sm" variant="secondary" onClick={() => setWipeOpen(true)}
          className="border-red-800 text-red-300 hover:bg-red-900/40">
          Запит на видалення…
        </Button>
      </section>

      {wipeOpen && <WipeDialog onClose={() => setWipeOpen(false)} />}
    </div>
  );
}
