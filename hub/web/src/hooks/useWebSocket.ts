import { useCallback, useEffect, useRef, useState } from "react";
import type { Event } from "../types";

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events`;

// Duration (ms) after reconnect during which arriving events are counted as "missed"
const REPLAY_WINDOW_MS = 2000;

export function useWebSocket(): {
  events: Event[];
  connected: boolean;
  missedCount: number;
  clearMissed: () => void;
} {
  const [events, setEvents] = useState<Event[]>([]);
  const [connected, setConnected] = useState(false);
  const [missedCount, setMissedCount] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const lastIdRef = useRef<string | null>(null);
  // Whether we are currently inside the replay window after a ?since= reconnect
  const replayWindowRef = useRef(false);
  const replayCountRef = useRef(0);
  const replayTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearMissed = useCallback(() => setMissedCount(0), []);

  useEffect(() => {
    function connect() {
      const isReconnect = lastIdRef.current !== null;
      const url = isReconnect ? `${WS_URL}?since=${lastIdRef.current}` : WS_URL;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        if (isReconnect) {
          // Start replay-window: count events that arrive in the first 2 s
          replayWindowRef.current = true;
          replayCountRef.current = 0;
          if (replayTimerRef.current) clearTimeout(replayTimerRef.current);
          replayTimerRef.current = setTimeout(() => {
            replayWindowRef.current = false;
            const count = replayCountRef.current;
            if (count > 0) setMissedCount(count);
            replayCountRef.current = 0;
          }, REPLAY_WINDOW_MS);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (replayTimerRef.current) {
          clearTimeout(replayTimerRef.current);
          replayTimerRef.current = null;
        }
        replayWindowRef.current = false;
        setTimeout(connect, 3000);
      };

      ws.onerror = () => ws.close();

      ws.onmessage = (e) => {
        try {
          const event: Event = JSON.parse(e.data as string);
          // ignore ping frames which don't have a proper id
          if (!event.id) return;
          lastIdRef.current = event.id;

          if (replayWindowRef.current) {
            replayCountRef.current += 1;
          }

          setEvents((prev) => [event, ...prev].slice(0, 200));
          saveToIDB(event);
        } catch {
          /* ignore malformed */
        }
      };
    }

    loadFromIDB().then((cached) => {
      if (cached.length) setEvents(cached);
    });
    connect();

    return () => {
      if (replayTimerRef.current) clearTimeout(replayTimerRef.current);
      wsRef.current?.close();
    };
  }, []);

  return { events, connected, missedCount, clearMissed };
}

// --- IndexedDB helpers ---
async function openDB(): Promise<IDBDatabase> {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open("iot-hub", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("events", { keyPath: "id" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function saveToIDB(event: Event): Promise<void> {
  const db = await openDB();
  const tx = db.transaction("events", "readwrite");
  tx.objectStore("events").put(event);
}

async function loadFromIDB(): Promise<Event[]> {
  const db = await openDB();
  return new Promise((resolve) => {
    const tx = db.transaction("events", "readonly");
    const req = tx.objectStore("events").getAll();
    req.onsuccess = () => {
      const all: Event[] = req.result as Event[];
      resolve(all.sort((a, b) => b.timestamp.localeCompare(a.timestamp)).slice(0, 200));
    };
    req.onerror = () => resolve([]);
  });
}
