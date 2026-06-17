import { useEffect, useState } from "react";
import { Play } from "lucide-react";
import { Dialog } from "../../components/Dialog";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { api } from "../../lib/api";
import type { Scene } from "../../features/scenes/scenes";

// ── Types ───────────────────────────────────────────────────────────────────

interface TryResult {
  matched_rule: string;
  action_class: string;
  reason: string;
  latency_ms: number;
  inferred_tool?: string | null;
}

// Badge colours consistent with the app's warm-residential palette.
// Kept local (not from assistant/shared) so the dialog stays self-contained.
const BADGE_COLORS: Record<string, string> = {
  AUTO: "bg-emerald-500/20 text-emerald-300",
  CONFIRM: "bg-amber-500/20 text-amber-300",
  DENY: "bg-red-500/20 text-red-300",
};

function badgeClass(actionClass: string): string {
  return BADGE_COLORS[actionClass] ?? "bg-slate-500/20 text-slate-300";
}

const VERDICT_TEXT: Record<string, string> = {
  AUTO: "Виконається одразу.",
  CONFIRM: "Знадобиться підтвердження під час виконання.",
  DENY: "Політика заблокує цю дію.",
};

// ── Props ────────────────────────────────────────────────────────────────────

export interface ScenePreviewDialogProps {
  scene: Scene | null; // null = closed
  onClose: () => void;
  onConfirm: (scene: Scene) => void; // parent calls useRunScene().run
  running: boolean; // true while the scene is being dispatched
}

// ── Component ────────────────────────────────────────────────────────────────

export function ScenePreviewDialog({
  scene,
  onClose,
  onConfirm,
  running,
}: ScenePreviewDialogProps) {
  const [dryRun, setDryRun] = useState<TryResult | null>(null);
  const [dryRunPending, setDryRunPending] = useState(false);

  // Run the policy dry-run whenever the scene changes (i.e. dialog opens or switches).
  useEffect(() => {
    if (!scene) {
      // Reset state when dialog closes.
      setDryRun(null);
      setDryRunPending(false);
      return;
    }

    let cancelled = false;
    setDryRun(null);
    setDryRunPending(true);

    api
      .post<TryResult>("/api/agent/try", { intent_text: scene.intent }, true)
      .then((result) => {
        if (!cancelled) {
          setDryRun(result);
        }
      })
      .catch(() => {
        // Silently fail — the run button stays enabled (except DENY check won't block)
        if (!cancelled) {
          setDryRun(null);
        }
      })
      .finally(() => {
        if (!cancelled) setDryRunPending(false);
      });

    return () => {
      cancelled = true;
    };
  }, [scene?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const isOpen = scene !== null;
  const isDenied = dryRun?.action_class === "DENY";
  const canRun = !running && !dryRunPending && !isDenied;

  function handleConfirm() {
    if (!scene || !canRun) return;
    onConfirm(scene);
    // Close immediately — useRunScene shows its own success/error toast.
    onClose();
  }

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={scene ? `${scene.icon} ${scene.name}` : undefined}
    >
      {scene && (
        <div className="space-y-5">
          {/* ── What will happen ─────────────────────────────────────── */}
          <section className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-[color:var(--text-muted)]">
              Що відбудеться
            </p>
            <p className="text-sm text-[color:var(--text)]">{scene.description}</p>
            <p
              className="rounded-lg border border-[color:var(--border)] bg-[color:var(--raised)] px-3 py-2 font-mono text-xs text-[color:var(--text-muted)]"
              aria-label="Команда агенту"
            >
              {scene.intent}
            </p>
          </section>

          {/* ── Policy dry-run verdict ───────────────────────────────── */}
          <section className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-[color:var(--text-muted)]">
              Перевірка політики
            </p>

            {dryRunPending && (
              <div className="flex items-center gap-2 text-xs text-[color:var(--text-muted)]">
                <Spinner className="h-4 w-4" />
                <span>Перевіряємо…</span>
              </div>
            )}

            {!dryRunPending && dryRun && (
              <div className="rounded-lg border border-[color:var(--border)] bg-[color:var(--raised)] p-3 space-y-2">
                {/* Badge row */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-bold ${badgeClass(dryRun.action_class)}`}
                  >
                    {dryRun.action_class}
                  </span>
                  {dryRun.matched_rule && (
                    <span className="text-xs text-[color:var(--text-muted)] truncate max-w-[220px]">
                      {dryRun.matched_rule}
                    </span>
                  )}
                </div>

                {/* Reason */}
                {dryRun.reason && (
                  <p className="text-xs text-[color:var(--text-muted)]">{dryRun.reason}</p>
                )}

                {/* Verdict helper text */}
                {VERDICT_TEXT[dryRun.action_class] && (
                  <p
                    className={`text-xs font-medium ${
                      isDenied
                        ? "text-red-400"
                        : dryRun.action_class === "CONFIRM"
                          ? "text-amber-400"
                          : "text-emerald-400"
                    }`}
                  >
                    {VERDICT_TEXT[dryRun.action_class]}
                  </p>
                )}
              </div>
            )}

            {!dryRunPending && !dryRun && (
              <p className="text-xs text-[color:var(--text-muted)]">
                Не вдалося отримати відповідь від системи політики.
              </p>
            )}
          </section>

          {/* ── Footer ──────────────────────────────────────────────── */}
          <div className="flex items-center justify-end gap-2 pt-1 border-t border-[color:var(--border)]">
            <Button variant="ghost" size="sm" onClick={onClose} disabled={running}>
              Скасувати
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!canRun}
              onClick={handleConfirm}
              aria-label={`Запустити сцену «${scene.name}»`}
            >
              {running ? (
                <>
                  <Spinner className="h-4 w-4" />
                  Запуск…
                </>
              ) : (
                <>
                  <Play size={14} />
                  Запустити
                </>
              )}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
