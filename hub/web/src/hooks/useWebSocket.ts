import { useCallback, useEffect, useRef, useState } from "react";
import type { HubEvent } from "../lib/types";
import { api } from "../lib/api";

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events`;

// Duration (ms) after reconnect during which arriving events are counted as "missed"
const REPLAY_WINDOW_MS = 2000;

export function useWebSocket(): {
  events: HubEvent[];
  connected: boolean;
  missedCount: number;
  clearMissed: () => void;
} {
  const [events, setHubEvents] = useState<HubEvent[]>([]);
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
          const event: HubEvent = JSON.parse(e.data as string);
          // ignore ping frames which don't have a proper id
          if (!event.id) return;
          lastIdRef.current = event.id;

          if (replayWindowRef.current) {
            replayCountRef.current += 1;
          }

          setHubEvents((prev) => [event, ...prev].slice(0, 200));
          saveToIDB(event);
        } catch {
          /* ignore malformed */
        }
      };
    }

    // Load seed data: merge IDB cache + REST API (REST wins on id collisions).
    Promise.all([
      loadFromIDB(),
      api.get<HubEvent[]>("/api/events?limit=100", true).catch(() => [] as HubEvent[]),
    ]).then(([cached, fetched]) => {
      const byId = new Map<string, HubEvent>();
      for (const e of cached) byId.set(e.id, e);
      for (const e of fetched) byId.set(e.id, e);
      const merged = [...byId.values()].sort((a, b) =>
        b.timestamp.localeCompare(a.timestamp),
      );
      if (merged.length) setHubEvents(merged);
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

async function saveToIDB(event: HubEvent): Promise<void> {
  const db = await openDB();
  const tx = db.transaction("events", "readwrite");
  tx.objectStore("events").put(event);
}

async function loadFromIDB(): Promise<HubEvent[]> {
  const db = await openDB();
  return new Promise((resolve) => {
    const tx = db.transaction("events", "readonly");
    const req = tx.objectStore("events").getAll();
    req.onsuccess = () => {
      const all: HubEvent[] = req.result as HubEvent[];
      resolve(all.sort((a, b) => b.timestamp.localeCompare(a.timestamp)).slice(0, 200));
    };
    req.onerror = () => resolve([]);
  });
}
