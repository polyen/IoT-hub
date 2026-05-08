import { useWebSocket } from "../hooks/useWebSocket";
import AlertCard from "../components/AlertCard";

export default function EventsFeed() {
  const { events, connected } = useWebSocket();

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">Стрічка подій</h1>
        <span
          className={`text-xs px-2 py-1 rounded-full ${
            connected ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"
          }`}
        >
          {connected ? "● підключено" : "○ з'єднання..."}
        </span>
      </div>
      {events.length === 0 ? (
        <p className="text-slate-400 text-center py-12">Немає подій</p>
      ) : (
        <div className="space-y-3">
          {events.map((event) => (
            <AlertCard key={event.id} event={event} />
          ))}
        </div>
      )}
    </div>
  );
}
