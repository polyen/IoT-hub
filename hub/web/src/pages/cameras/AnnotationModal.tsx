import { useRef, useEffect, useState, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { X, Trash2, Save } from "lucide-react";
import { api } from "../../lib/api";
import { Dialog } from "../../components/Dialog";
import { Button } from "../../components/Button";

const CLASSES = [
  { id: 0, label: "Людина", color: "#3b82f6" },
  { id: 1, label: "Вогонь", color: "#ef4444" },
  { id: 2, label: "Дим", color: "#94a3b8" },
] as const;

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
  classId: number;
}

interface Props {
  imageDataUrl: string;
  onClose: () => void;
}

export function AnnotationModal({ imageDataUrl, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [currentClassId, setCurrentClassId] = useState<number>(1); // default: fire
  const drawingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });
  const [preview, setPreview] = useState<Box | null>(null);

  useEffect(() => {
    const img = new Image();
    img.onload = () => {
      imgRef.current = img;
      const canvas = canvasRef.current;
      if (canvas) {
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
      }
      setImgLoaded(true);
    };
    img.src = imageDataUrl;
  }, [imageDataUrl]);

  const redraw = useCallback((finalBoxes: Box[], previewBox: Box | null) => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.drawImage(img, 0, 0);

    const fontSize = Math.max(14, canvas.width / 45);
    const lineW = Math.max(2, canvas.width / 320);

    for (const b of previewBox ? [...finalBoxes, previewBox] : finalBoxes) {
      const cls = CLASSES[b.classId as 0 | 1 | 2] ?? CLASSES[0];
      ctx.strokeStyle = cls.color;
      ctx.lineWidth = lineW;
      ctx.strokeRect(b.x, b.y, b.w, b.h);
      ctx.fillStyle = cls.color + "33";
      ctx.fillRect(b.x, b.y, b.w, b.h);

      ctx.font = `bold ${fontSize}px sans-serif`;
      const textW = ctx.measureText(cls.label).width;
      const labelY = b.y > fontSize + 6 ? b.y - 4 : b.y + fontSize + 4;
      ctx.fillStyle = cls.color + "cc";
      ctx.fillRect(b.x, labelY - fontSize - 2, textW + 8, fontSize + 6);
      ctx.fillStyle = "#ffffff";
      ctx.fillText(cls.label, b.x + 4, labelY);
    }
  }, []);

  useEffect(() => {
    if (imgLoaded) redraw(boxes, preview);
  }, [boxes, preview, imgLoaded, redraw]);

  const getPos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) * canvas.width) / rect.width,
      y: ((e.clientY - rect.top) * canvas.height) / rect.height,
    };
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const pos = getPos(e);
    startPosRef.current = pos;
    drawingRef.current = true;
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current) return;
    const pos = getPos(e);
    const sx = startPosRef.current.x;
    const sy = startPosRef.current.y;
    setPreview({
      x: Math.min(sx, pos.x),
      y: Math.min(sy, pos.y),
      w: Math.abs(pos.x - sx),
      h: Math.abs(pos.y - sy),
      classId: currentClassId,
    });
  };

  const handleMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    const pos = getPos(e);
    const sx = startPosRef.current.x;
    const sy = startPosRef.current.y;
    const w = Math.abs(pos.x - sx);
    const h = Math.abs(pos.y - sy);
    if (w > 8 && h > 8) {
      setBoxes((prev) => [
        ...prev,
        { x: Math.min(sx, pos.x), y: Math.min(sy, pos.y), w, h, classId: currentClassId },
      ]);
    }
    setPreview(null);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const canvas = canvasRef.current!;
      const W = canvas.width;
      const H = canvas.height;
      return api.post("/api/cv/annotate", {
        image_b64: imageDataUrl.split(",")[1] ?? imageDataUrl,
        boxes: boxes.map((b) => ({
          class_id: b.classId,
          cx: (b.x + b.w / 2) / W,
          cy: (b.y + b.h / 2) / H,
          w: b.w / W,
          h: b.h / H,
        })),
      });
    },
    onSuccess: () => {
      toast.success("Розмітку збережено в датасет");
      onClose();
    },
    onError: () => toast.error("Помилка збереження розмітки"),
  });

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Розмітка кадру"
      className="max-w-2xl"
    >
      <div className="flex gap-2 mb-3 flex-wrap items-center">
        {CLASSES.map((cls) => (
          <button
            key={cls.id}
            onClick={() => setCurrentClassId(cls.id)}
            className="px-3 py-1 text-xs rounded-lg border transition-all"
            style={
              currentClassId === cls.id
                ? { backgroundColor: cls.color + "33", borderColor: cls.color, color: cls.color }
                : { borderColor: "var(--border)", color: "var(--text-muted)" }
            }
          >
            {cls.label}
          </button>
        ))}
        <span className="ml-auto text-xs text-[color:var(--text-faint)]">
          Намалюй рамку → оберіть клас зверху
        </span>
      </div>

      <canvas
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => {
          drawingRef.current = false;
          setPreview(null);
        }}
        className="w-full rounded-xl border border-[color:var(--border)] cursor-crosshair select-none"
      />

      {boxes.length > 0 && (
        <ul className="mt-2 space-y-1 max-h-28 overflow-y-auto">
          {boxes.map((b, i) => {
            const cls = CLASSES[b.classId as 0 | 1 | 2];
            return (
              <li
                key={i}
                className="flex items-center justify-between text-xs px-2 py-1 rounded bg-[color:var(--raised)]"
              >
                <span style={{ color: cls?.color }} className="font-medium">
                  {cls?.label ?? "?"}
                </span>
                <span className="text-[color:var(--text-faint)]">
                  {Math.round(b.w)}×{Math.round(b.h)} px
                </span>
                <button
                  onClick={() => setBoxes((prev) => prev.filter((_, j) => j !== i))}
                  className="text-[color:var(--text-faint)] hover:text-red-400 transition-colors"
                >
                  <X size={12} />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="flex gap-2 justify-end mt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Скасувати
        </Button>
        {boxes.length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setBoxes([])} className="text-red-400">
            <Trash2 size={14} />
            Очистити
          </Button>
        )}
        <Button
          variant="primary"
          size="sm"
          disabled={boxes.length === 0 || saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          <Save size={14} />
          {saveMutation.isPending ? "Зберігаємо…" : `Зберегти (${boxes.length})`}
        </Button>
      </div>
    </Dialog>
  );
}
