import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Shield, ShieldOff, ShieldCheck, ShieldAlert } from "lucide-react";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { relativeTime, shortDateTime } from "../../lib/format";
import { useDecideConfirm } from "../../features/confirm/useDecideConfirm";
import { countdownSeconds } from "../../lib/format";
import type { SecurityState, SecurityEvent, ConfirmRequest } from "../../lib/types";

type SecurityAction = "arm_home" | "arm_away" | "disarm";

const MODE_CONFIG = {
  disarmed: {
    label: "Охорона вимкнена",
    sublabel: "Система в режимі спостереження",
    Icon: ShieldOff,
    color: "text-slate-400",
    border: "border-slate-700",
    bg: "bg-slate-800/60",
    dot: "bg-slate-500",
  },
  armed_home: {
    label: "Охорона: вдома",
    sublabel: "Периметр захищено, рух усередині дозволено",
    Icon: ShieldCheck,
    color: "text-green-400",
    border: "border-green-800",
    bg: "bg-green-950/40",
    dot: "bg-green-500",
  },
  armed_away: {
    label: "Охорона: відсутній",
    sublabel: "Повний захист, будь-який рух — тривога",
    Icon: ShieldAlert,
    color: "text-amber-400",
    border: "border-amber-800",
    bg: "bg-amber-950/30",
    dot: "bg-amber-500",
  },
} as const;

const EVENT_LABELS: Record<string, string> = {
  person_detected: "Людина виявлена",
  motion: "Рух",
  fire: "Вогонь",
  smoke: "Дим",
  alarm: "Тривога",
  fall_detected: "Падіння",
};

function InlineConfirm({ confirmId, onDone }: { confirmId: string; onDone: () => void }) {
  const { data: req } = useQuery<ConfirmRequest>({
    queryKey: ["confirm", confirmId],
    queryFn: () => api.get<ConfirmRequest>(`/api/confirm/${confirmId}`),
    refetchInterval: 2_000,
  });
  const { mutate: decide, isPending } = useDecideConfirm();
  const seconds = req ? countdownSeconds(req.expires_at) : 30;
  const pct = Math.max(0, (seconds / 30) * 100);

  const handle = (decision: "approve" | "reject") => {
    decide({ id: confirmId, decision }, { onSuccess: onDone });
  };

  return (
    <div className="rounded-xl border border-amber-700 bg-amber-950/40 p-4 space-y-3">
      <div className="h-1 rounded-full bg-slate-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-1000 ${pct > 50 ? "bg-green-500" : pct > 20 ? "bg-amber-500" : "bg-red-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-sm text-amber-200">
        {req?.confirm_message ?? "Підтверди зміну режиму охорони"}
        <span className="ml-2 font-mono text-slate-400 text-xs">{seconds}с</span>
      </p>
      <div className="flex gap-2">
        <Button variant="primary" size="sm" className="flex-1" disabled={isPending || seconds === 0} onClick={() => handle("approve")}>
          Підтвердити
        </Button>
        <Button variant="danger" size="sm" className="flex-1" disabled={isPending} onClick={() => handle("reject")}>
          Скасувати
        </Button>
      </div>
    </div>
  );
}

export default function SecurityPage() {
  const qc = useQueryClient();
  const [pendingConfirmId, setPendingConfirmId] = useState<string | null>(null);

  const { data: state, isLoading } = useQuery<SecurityState>({
    queryKey: ["security-state"],
    queryFn: () => api.get<SecurityState>("/api/security/state"),
    refetchInterval: 15_000,
  });

  const { data: events } = useQuery<SecurityEvent[]>({
    queryKey: ["security-events"],
    queryFn: () => api.get<SecurityEvent[]>("/api/security/events?limit=15"),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  const commandMutation = useMutation({
    mutationFn: (action: SecurityAction) =>
      api.post<{ result: string; confirm_id?: string }>("/api/security/command", { action }),
    onSuccess: (data) => {
      if (data.result === "confirm_required" && data.confirm_id) {
        setPendingConfirmId(data.confirm_id);
        toast.info("Потрібне підтвердження");
      } else if (data.result === "executed") {
        toast.success("Режим охорони змінено");
        qc.invalidateQueries({ queryKey: ["security-state"] });
      }
    },
    onError: (err: Error) => toast.error(err.message ?? "Помилка"),
  });

  const mode = state?.mode ?? "disarmed";
  const cfg = MODE_CONFIG[mode] ?? MODE_CONFIG.disarmed;
  const { Icon } = cfg;

  const ACTIONS: { action: SecurityAction; label: string; variant: "primary" | "secondary" | "danger" }[] = [
    { action: "arm_home", label: "Охорона: вдома", variant: "primary" },
    { action: "arm_away", label: "Охорона: відсутній", variant: "secondary" },
    { action: "disarm", label: "Вимкнути охорону", variant: "danger" },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      <h1 className="text-xl font-semibold flex items-center gap-2">
        <Shield size={20} className="text-primary-400" />
        Безпека
      </h1>

      {/* Current mode card */}
      {isLoading ? (
        <div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div>
      ) : (
        <div className={`rounded-xl border ${cfg.border} ${cfg.bg} p-5`}>
          <div className="flex items-center gap-4">
            <div className={`p-3 rounded-full border ${cfg.border} ${cfg.bg}`}>
              <Icon size={28} className={cfg.color} />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${cfg.dot} animate-pulse`} />
                <p className={`font-semibold text-base ${cfg.color}`}>{cfg.label}</p>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">{cfg.sublabel}</p>
              {state?.since && (
                <p className="text-xs text-slate-500 mt-1">
                  Змінено {relativeTime(state.since)}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Inline confirm */}
      {pendingConfirmId && (
        <InlineConfirm
          confirmId={pendingConfirmId}
          onDone={() => {
            setPendingConfirmId(null);
            qc.invalidateQueries({ queryKey: ["security-state"] });
          }}
        />
      )}

      {/* Action buttons */}
      {!pendingConfirmId && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Змінити режим
          </h2>
          <div className="grid grid-cols-1 gap-2">
            {ACTIONS.map(({ action, label, variant }) => (
              <Button
                key={action}
                variant={variant}
                disabled={commandMutation.isPending || mode === (action === "disarm" ? "disarmed" : action === "arm_home" ? "armed_home" : "armed_away")}
                onClick={() => commandMutation.mutate(action)}
                className="justify-start gap-3 py-3"
              >
                {commandMutation.isPending && commandMutation.variables === action ? (
                  <Spinner className="h-4 w-4" />
                ) : (
                  <Shield size={16} />
                )}
                {label}
                {mode === (action === "disarm" ? "disarmed" : action === "arm_home" ? "armed_home" : "armed_away") && (
                  <span className="ml-auto text-xs opacity-50">активно</span>
                )}
              </Button>
            ))}
          </div>
        </section>
      )}

      {/* Recent events */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Останні події безпеки
        </h2>
        {!events?.length ? (
          <div className="rounded-xl border border-slate-700 bg-slate-800/40 px-4 py-6 text-center text-xs text-slate-500">
            Подій ще не було
          </div>
        ) : (
          <div className="rounded-xl border border-slate-700 bg-slate-800/60 divide-y divide-slate-700">
            {events.map((ev) => (
              <div key={ev.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <span
                  className={`h-2 w-2 rounded-full shrink-0 ${
                    ev.type === "fire" || ev.type === "alarm" ? "bg-red-500" :
                    ev.type === "smoke" || ev.type === "fall_detected" ? "bg-amber-500" :
                    "bg-blue-500"
                  }`}
                />
                <span className="flex-1 text-slate-300">
                  {EVENT_LABELS[ev.type] ?? ev.type}
                  {ev.room && <span className="text-slate-500 ml-1.5">· {ev.room}</span>}
                </span>
                <span className="text-xs text-slate-500 shrink-0">{shortDateTime(ev.timestamp)}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
