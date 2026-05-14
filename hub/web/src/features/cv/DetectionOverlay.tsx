import { useEffect, useRef } from "react";
import type { CvFrame } from "../../lib/types";

interface Props {
  frame: CvFrame | null;
  videoWidth: number;
  videoHeight: number;
  visible: boolean;
}

const CLS_COLORS: Record<string, string> = {
  person: "#22c55e",
  stranger: "#ef4444",
  face: "#3b82f6",
  fall: "#f59e0b",
};

export function DetectionOverlay({ frame, videoWidth, videoHeight, visible }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!frame) return;

    canvas.width = videoWidth;
    canvas.height = videoHeight;

    for (const det of frame.dets ?? []) {
      const [x1, y1, x2, y2] = det.bbox;
      const rx = x1 * videoWidth;
      const ry = y1 * videoHeight;
      const rw = (x2 - x1) * videoWidth;
      const rh = (y2 - y1) * videoHeight;

      const color = CLS_COLORS[det.cls] ?? "#94a3b8";
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(rx, ry, rw, rh);

      const label = `${det.cls}${det.face_id ? ` (${det.face_id})` : ""}${det.conf ? ` ${Math.round(det.conf * 100)}%` : ""}`;
      ctx.fillStyle = color;
      ctx.font = "12px monospace";
      ctx.fillText(label, rx + 4, ry + 14);
    }
  }, [frame, videoWidth, videoHeight, visible]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      style={{ display: visible ? "block" : "none" }}
    />
  );
}
