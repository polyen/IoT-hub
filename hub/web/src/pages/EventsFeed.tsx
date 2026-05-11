import { useWebSocket } from "../hooks/useWebSocket";
import AlertCard from "../components/AlertCard";

export default function EventsFeed() {
  const { events, connected, missedCount, clearMissed } = useWebSocket();

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

      {missedCount > 0 && (
        <div className="flex items-center justify-between bg-blue-900 border border-blue-700 rounded-lg px-4 py-2 mb-4">
          <span className="text-sm text-blue-200">
            {missedCount} подій пропущено під час офлайн
          </span>
          <button
            onClick={clearMissed}
            className="text-blue-400 hover:text-white text-sm ml-4"
          >
            Закрити
          </button>
        </div>
      )}

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
