import { useEffect, useRef, useState } from "react";
import type { CvFrame } from "../../lib/types";

export function useCameraStream(cameraId: string | null) {
  const [lastFrame, setLastFrame] = useState<CvFrame | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!cameraId) return;
    const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/cv/${cameraId}`;
    let destroyed = false;

    function connect() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onmessage = (e) => {
        try {
          const frame: CvFrame = JSON.parse(e.data as string);
          if (!destroyed) setLastFrame(frame);
        } catch {}
      };
      ws.onclose = () => {
        if (!destroyed) setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      destroyed = true;
      wsRef.current?.close();
    };
  }, [cameraId]);

  return lastFrame;
}
