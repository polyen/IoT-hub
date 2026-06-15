import { useState } from "react";
import { Bell } from "lucide-react";
import { useConfirmStream } from "../../features/confirm/useConfirmStream";
import { ConfirmCard } from "../../pages/confirm/ConfirmCard";
import { Sheet } from "../../components/Dialog";
import { EmptyState } from "../../components/EmptyState";

interface Props {
  /** Extra classes for the trigger button (e.g. positioning on mobile). */
  className?: string;
}

/**
 * Global pending-confirmations affordance: a bell with a live count badge that
 * opens a sheet listing the actuator confirmations awaiting a decision.
 * Replaces the former bottom-nav "Confirm" tab — one WebSocket via useConfirmStream.
 */
export function ConfirmBell({ className }: Props) {
  const [open, setOpen] = useState(false);
  const { pending, connected, removePending } = useConfirmStream();
  const count = pending.length;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="Підтвердження"
        className={`relative p-2 rounded-lg text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)] transition-colors ${className ?? ""}`}
      >
        <Bell size={18} className={count > 0 ? "text-amber-400" : undefined} />
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-red-600 text-[10px] font-bold text-white px-1 pointer-events-none animate-pulse">
            {count > 9 ? "9+" : count}
          </span>
        )}
      </button>

      <Sheet open={open} onOpenChange={setOpen} title="Підтвердження">
        <div className="flex items-center justify-end mb-2">
          <span
            className={`text-xs px-2 py-0.5 rounded-full ${
              connected ? "bg-green-900 text-green-300" : "bg-slate-800 text-slate-500"
            }`}
          >
            {connected ? "● live" : "○ ..."}
          </span>
        </div>
        {count === 0 ? (
          <EmptyState message="Немає запитів на підтвердження" icon="✓" />
        ) : (
          <div className="space-y-3">
            {pending.map((req) => (
              <ConfirmCard key={req.id} request={req} onDone={() => removePending(req.id)} />
            ))}
          </div>
        )}
      </Sheet>
    </>
  );
}
