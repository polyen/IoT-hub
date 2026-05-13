import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { clsx } from "clsx";
import { Button } from "../../components/Button";
import { useDecideConfirm } from "../../features/confirm/useDecideConfirm";
import { countdownSeconds } from "../../lib/format";
import type { ConfirmRequest } from "../../lib/types";

interface Props {
  request: ConfirmRequest;
  onDone: () => void;
}

export function ConfirmCard({ request, onDone }: Props) {
  const { t } = useTranslation("common");
  const { mutate: decide, isPending } = useDecideConfirm();
  const [seconds, setSeconds] = useState(() => countdownSeconds(request.expires_at));

  useEffect(() => {
    if (seconds <= 0) return;
    const id = setInterval(() => {
      setSeconds(countdownSeconds(request.expires_at));
    }, 1000);
    return () => clearInterval(id);
  }, [request.expires_at, seconds]);

  const pct = Math.max(0, (seconds / 60) * 100);

  const handle = (decision: "approve" | "reject") => {
    if ("vibrate" in navigator) navigator.vibrate(decision === "approve" ? [50] : [50, 50, 50]);
    decide({ id: request.id, decision }, { onSuccess: onDone });
  };

  return (
    <div className="bg-slate-800 light:bg-white rounded-xl border border-amber-700 p-4 space-y-3">
      {/* countdown bar */}
      <div className="h-1 rounded-full bg-slate-700 overflow-hidden">
        <div
          className={clsx(
            "h-full rounded-full transition-all duration-1000",
            pct > 50 ? "bg-green-500" : pct > 20 ? "bg-amber-500" : "bg-red-500",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium leading-snug">{request.confirm_message}</p>
          <p className="text-xs text-slate-500 mt-1">
            {request.tool}
            {request.schedule_origin && (
              <span className="ml-2 text-amber-600">· {request.schedule_origin}</span>
            )}
          </p>
          <p className="text-xs text-slate-400 mt-1 truncate italic">{request.intent_text}</p>
        </div>
        <span className="text-sm font-mono text-slate-400 shrink-0">{seconds}с</span>
      </div>

      <div className="flex gap-2">
        <Button
          variant="primary"
          size="sm"
          className="flex-1"
          disabled={isPending || seconds === 0}
          onClick={() => handle("approve")}
        >
          {t("action.approve")}
        </Button>
        <Button
          variant="danger"
          size="sm"
          className="flex-1"
          disabled={isPending}
          onClick={() => handle("reject")}
        >
          {t("action.reject")}
        </Button>
      </div>

      {seconds === 0 && (
        <p className="text-xs text-slate-500 text-center">Час вийшов — автоматично відхилено</p>
      )}
    </div>
  );
}
