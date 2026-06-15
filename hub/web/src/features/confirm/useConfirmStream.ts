import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { ConfirmRequest } from "../../lib/types";

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/confirm`;

let globalCount = 0;
const listeners = new Set<(n: number) => void>();

function notifyCount(n: number) {
  globalCount = n;
  listeners.forEach((fn) => fn(n));
}

export function useConfirmCount(): number {
  const [count, setCount] = useState(globalCount);
  useEffect(() => {
    listeners.add(setCount);
    return () => { listeners.delete(setCount); };
  }, []);
  return count;
}

export function useConfirmStream(): {
  pending: ConfirmRequest[];
  connected: boolean;
  removePending: (id: string) => void;
} {
  const [pending, setPending] = useState<ConfirmRequest[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let destroyed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    // Exponential backoff so a globally-mounted bell doesn't hammer /ws/confirm
    // every 3 s while the hub is unreachable (ERR_NAME_NOT_RESOLVED floods).
    let retryDelay = 2000;
    const MAX_DELAY = 30_000;

    async function fetchPending() {
      try {
        const res = await fetch("/api/confirm/pending");
        if (res.ok) {
          const data: ConfirmRequest[] = await res.json();
          if (!destroyed) {
            setPending(data);
            notifyCount(data.length);
          }
        }
      } catch {}
    }

    fetchPending();

    function scheduleReconnect() {
      if (destroyed) return;
      reconnectTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, MAX_DELAY); // 2→4→8→16→30s cap
    }

    function connect() {
      if (destroyed || navigator.onLine === false) {
        // No point dialing while the browser knows it's offline — wait for 'online'.
        scheduleReconnect();
        return;
      }
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => {
        if (!destroyed) {
          setConnected(true);
          retryDelay = 2000; // reset backoff on a healthy connection
        }
      };
      ws.onclose = () => {
        if (!destroyed) {
          setConnected(false);
          scheduleReconnect();
        }
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try {
          const msg: ConfirmRequest | { type: "ping" } = JSON.parse(e.data as string);
          if ("type" in msg && msg.type === "ping") return;
          const req = msg as ConfirmRequest;
          setPending((prev) => {
            const filtered = prev.filter((p) => p.id !== req.id);
            const next = req.state === "pending" ? [req, ...filtered] : filtered;
            notifyCount(next.length);
            return next;
          });
          if (req.state === "pending") {
            toast.warning(`⚠️ ${req.confirm_message}`, { duration: 10_000, id: req.id });
            if ("Notification" in window && Notification.permission === "granted") {
              new Notification("IoT Hub — потрібне підтвердження", { body: req.confirm_message, tag: req.id });
            }
          }
        } catch {}
      };
    }

    connect();

    // When connectivity returns, reconnect immediately instead of waiting out the backoff.
    const onOnline = () => {
      retryDelay = 2000;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (wsRef.current?.readyState !== WebSocket.OPEN) {
        wsRef.current?.close();
        connect();
      }
    };
    window.addEventListener("online", onOnline);

    return () => {
      destroyed = true;
      window.removeEventListener("online", onOnline);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, []);

  function removePending(id: string) {
    setPending((prev) => {
      const next = prev.filter((p) => p.id !== id);
      notifyCount(next.length);
      return next;
    });
  }

  return { pending, connected, removePending };
}
