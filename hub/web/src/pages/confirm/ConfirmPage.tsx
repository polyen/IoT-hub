import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmCard } from "./ConfirmCard";
import { useConfirmStream } from "../../features/confirm/useConfirmStream";
import { EmptyState } from "../../components/EmptyState";

export default function ConfirmPage() {
  const { t } = useTranslation("common");
  const { pending, connected, removePending } = useConfirmStream();

  useEffect(() => {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">{t("nav.confirm")}</h1>
        <span
          className={`text-xs px-2 py-1 rounded-full ${
            connected ? "bg-green-900 text-green-300" : "bg-slate-800 text-slate-500"
          }`}
        >
          {connected ? "● live" : "○ ..."}
        </span>
      </div>

      {pending.length === 0 ? (
        <EmptyState message="Немає запитів на підтвердження" icon="✓" />
      ) : (
        <div className="space-y-3">
          {pending.map((req) => (
            <ConfirmCard key={req.id} request={req} onDone={() => removePending(req.id)} />
          ))}
        </div>
      )}
    </div>
  );
}
