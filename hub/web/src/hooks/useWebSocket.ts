import { useEffect, useRef, useState } from "react";
import type { Event } from "../types";

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events`;

export function useWebSocket() {
  const [events, setEvents] = useState<Event[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const lastIdRef = useRef<string | null>(null);

  useEffect(() => {
    function connect() {
      const url = lastIdRef.current ? `${WS_URL}?since=${lastIdRef.current}` : WS_URL;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try {
          const event: Event = JSON.parse(e.data as string);
          // ignore ping frames which don't have a proper id
          if (!event.id) return;
          lastIdRef.current = event.id;
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
    return () => wsRef.current?.close();
  }, []);

  return { events, connected };
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
