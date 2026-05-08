import { useState } from "react";
import type { Event, FeedbackPayload } from "../types";

const PRESET_TAGS = ["свічка", "пара", "сонце", "відбиття", "інше"];

interface Props {
  event: Event;
}

export default function AlertCard({ event }: Props) {
  const isAlert = event.type === "alert" || event.type === "camera/event";
  const [feedback, setFeedback] = useState<"TP" | "FP" | "not_sure" | null>(null);
  const [tag, setTag] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  const submit = async (label: "TP" | "FP" | "not_sure") => {
    setFeedback(label);
    setSending(true);
    const body: FeedbackPayload = { alert_id: event.id, user_label: label };
    if (tag) body.tag = tag;
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setSent(true);
    } catch {
      setSending(false);
    }
  };

  const tierColor =
    (["bg-purple-900", "bg-slate-700", "bg-orange-900", "bg-red-900"] as const)[event.tier] ??
    "bg-slate-700";

  return (
    <div
      className={`rounded-lg border border-slate-700 bg-slate-800 p-4 ${
        isAlert ? "border-l-4 border-l-amber-500" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${tierColor}`}>
              T{event.tier}
            </span>
            {event.room && <span className="text-slate-400 text-sm">{event.room}</span>}
            <span className="font-medium">{event.type}</span>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            {new Date(event.timestamp).toLocaleString("uk-UA")}
          </p>
          {event.payload && (
            <pre className="text-xs text-slate-400 mt-2 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          )}
        </div>
      </div>

      {isAlert && !sent && (
        <div className="mt-3 pt-3 border-t border-slate-700">
          <p className="text-xs text-slate-400 mb-2">Оцінка:</p>
          <div className="flex flex-wrap gap-2">
            {(["TP", "FP", "not_sure"] as const).map((label) => (
              <button
                key={label}
                onClick={() => submit(label)}
                disabled={sending}
                className={`text-xs px-3 py-1.5 rounded transition-colors ${
                  feedback === label
                    ? "bg-blue-600 text-white"
                    : "bg-slate-700 hover:bg-slate-600 text-slate-300"
                } disabled:opacity-50`}
              >
                {label === "TP"
                  ? "✓ Реальна тривога"
                  : label === "FP"
                    ? "✗ Хибна тривога"
                    : "? Не впевнений"}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {PRESET_TAGS.map((t) => (
              <button
                key={t}
                onClick={() => setTag((prev) => (prev === t ? "" : t))}
                className={`text-xs px-2 py-1 rounded-full border transition-colors ${
                  tag === t
                    ? "border-blue-500 bg-blue-900 text-blue-200"
                    : "border-slate-600 text-slate-400 hover:border-slate-400"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      )}

      {sent && <p className="text-xs text-green-400 mt-2">Дякуємо за оцінку!</p>}
    </div>
  );
}
