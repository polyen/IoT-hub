import { useEffect, useRef } from "react";
import type { CvFrame } from "../../lib/types";

interface Props {
  frame: CvFrame | null;
  videoWidth: number;
  videoHeight: number;
  visible: boolean;
  onEnrollRequest?: (trackId: number, room: string, currentName: string) => void;
}

const CLS_COLORS: Record<string, string> = {
  person: "#22c55e",
  stranger: "#ef4444",
  uncertain_person: "#f59e0b",
  face: "#3b82f6",
  fall: "#f59e0b",
};

// COCO skeleton edge pairs (keypoint indices).
// Only the subset useful for visual confirmation of pose output.
const SKELETON_EDGES: [number, number][] = [
  [5, 6],   // shoulders
  [11, 12], // hips
  [5, 11],  // left side
  [6, 12],  // right side
  [5, 7], [7, 9],   // left arm
  [6, 8], [8, 10],  // right arm
  [11, 13], [13, 15], // left leg
  [12, 14], [14, 16], // right leg
];

function drawSkeleton(
  ctx: CanvasRenderingContext2D,
  kps: [number, number][],
  w: number,
  h: number,
  color: string,
): void {
  // Edges
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  for (const [a, b] of SKELETON_EDGES) {
    const pa = kps[a], pb = kps[b];
    if (!pa || !pb) continue;
    if (pa[0] === 0 && pa[1] === 0) continue;
    if (pb[0] === 0 && pb[1] === 0) continue;
    ctx.beginPath();
    ctx.moveTo(pa[0] * w, pa[1] * h);
    ctx.lineTo(pb[0] * w, pb[1] * h);
    ctx.stroke();
  }
  // Dots
  ctx.fillStyle = color;
  for (const [kx, ky] of kps) {
    if (kx === 0 && ky === 0) continue;
    ctx.beginPath();
    ctx.arc(kx * w, ky * h, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

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

    // Status bar: overlay lag + pose indicator
    if (frame.ts) {
      const frameAge = (Date.now() - new Date(frame.ts).getTime()) / 1000;
      const lagColor = frameAge > 5 ? "#ef4444" : frameAge > 2 ? "#f59e0b" : "#22c55e";
      const poseActive = (frame.dets ?? []).some((d) => d.kps && d.kps.length > 0);
      const statusText = `lag: ${frameAge.toFixed(1)}s  pose: ${poseActive ? "✓" : "–"}`;
      ctx.font = "11px monospace";
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillRect(4, 4, 160, 18);
      ctx.fillStyle = lagColor;
      ctx.fillText(statusText, 8, 17);
    }

    for (const det of frame.dets ?? []) {
      const [x1, y1, x2, y2] = det.bbox;
      const rx = x1 * videoWidth;
      const ry = y1 * videoHeight;
      const rw = (x2 - x1) * videoWidth;
      const rh = (y2 - y1) * videoHeight;

      const isUnknown = det.face_id === "unknown" || det.face_id === null;
      const isUncertain = typeof det.face_id === "string" && det.face_id.endsWith("?");
      const effectiveCls =
        det.cls === "person"
          ? isUnknown
            ? "stranger"
            : isUncertain
              ? "uncertain_person"
              : "person"
          : det.cls;
      const color = CLS_COLORS[effectiveCls] ?? "#94a3b8";
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(rx, ry, rw, rh);

      const label = `${det.cls}${det.face_id && det.face_id !== "unknown" ? ` (${det.face_id})` : ""}${det.conf ? ` ${Math.round(det.conf * 100)}%` : ""}`;
      ctx.fillStyle = color;
      ctx.font = "12px monospace";
      ctx.fillText(label, rx + 4, ry + 14);

      // Pose skeleton (drawn when keypoints available)
      if (det.kps && det.kps.length >= 13) {
        drawSkeleton(ctx, det.kps, videoWidth, videoHeight, "rgba(255,255,255,0.45)");
      }

      // Enroll / correct hint for all persons
      if (det.cls === "person" && onEnrollRequest) {
        const [hintColor, hintText] = isUnknown
          ? ["rgba(239,68,68,0.75)", "+ Назвати"]
          : isUncertain
            ? ["rgba(245,158,11,0.85)", "? Підтвердити"]
            : ["rgba(148,163,184,0.6)", "✎ Змінити"];
        ctx.fillStyle = hintColor;
        ctx.font = "bold 10px monospace";
        ctx.fillText(hintText, rx + 4, ry + rh - 6);
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
      if (det.track_id == null) continue;
      const [x1, y1, x2, y2] = det.bbox;
      if (nx >= x1 && nx <= x2 && ny >= y1 && ny <= y2) {
        // Strip "?" suffix so the dialog pre-fills with the base name
        const currentName =
          det.face_id && det.face_id !== "unknown"
            ? det.face_id.replace(/\?$/, "")
            : "";
        onEnrollRequest(det.track_id, frame.room, currentName);
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
