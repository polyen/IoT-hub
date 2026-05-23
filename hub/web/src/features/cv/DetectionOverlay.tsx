import { useEffect, useRef } from "react";
import type { CvFrame } from "../../lib/types";

interface Props {
  frame: CvFrame | null;
  videoWidth: number;
  videoHeight: number;
  visible: boolean;
  onEnrollRequest?: (trackId: number, room: string) => void;
}

const CLS_COLORS: Record<string, string> = {
  person: "#22c55e",
  stranger: "#ef4444",
  face: "#3b82f6",
  fall: "#f59e0b",
};

export function DetectionOverlay({
  frame,
  videoWidth,
  videoHeight,
  visible,
  onEnrollRequest,
}: Props) {
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

      const isStranger = det.face_id === "unknown" || det.face_id === null;
      const effectiveCls = isStranger && det.cls === "person" ? "stranger" : det.cls;
      const color = CLS_COLORS[effectiveCls] ?? "#94a3b8";
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(rx, ry, rw, rh);

      const label = `${det.cls}${det.face_id && det.face_id !== "unknown" ? ` (${det.face_id})` : ""}${det.conf ? ` ${Math.round(det.conf * 100)}%` : ""}`;
      ctx.fillStyle = color;
      ctx.font = "12px monospace";
      ctx.fillText(label, rx + 4, ry + 14);

      // Enroll hint for strangers
      if (isStranger && det.cls === "person" && onEnrollRequest) {
        ctx.fillStyle = "rgba(239,68,68,0.75)";
        ctx.font = "bold 10px monospace";
        ctx.fillText("+ Назвати", rx + 4, ry + rh - 6);
      }
    }
  }, [frame, videoWidth, videoHeight, visible, onEnrollRequest]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!frame || !onEnrollRequest) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;

    for (const det of frame.dets ?? []) {
      if (det.cls !== "person") continue;
      if (det.face_id && det.face_id !== "unknown") continue;
      if (det.track_id == null) continue;
      const [x1, y1, x2, y2] = det.bbox;
      if (nx >= x1 && nx <= x2 && ny >= y1 && ny <= y2) {
        onEnrollRequest(det.track_id, (frame as { room?: string }).room ?? "");
        return;
      }
    }
  };

  return (
    <canvas
      ref={canvasRef}
      onClick={onEnrollRequest ? handleClick : undefined}
      className="absolute inset-0 w-full h-full"
      style={{
        display: visible ? "block" : "none",
        pointerEvents: onEnrollRequest ? "auto" : "none",
        cursor: onEnrollRequest ? "crosshair" : "default",
      }}
    />
  );
}
