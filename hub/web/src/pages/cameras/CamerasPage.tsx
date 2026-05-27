import { useState, useRef } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Camera, Eye, EyeOff, Crosshair, ImageDown, PenLine, Users } from "lucide-react";
import { api } from "../../lib/api";
import { CameraLive, type CameraLiveHandle } from "./CameraLive";
import { AnnotationModal } from "./AnnotationModal";
import { EnrollmentsDialog } from "./EnrollmentsDialog";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import type { Camera as CameraType } from "../../lib/types";

const FEEDBACK_LABELS = [
  { value: "fp", label: "✗ Хибна тривога" },
  { value: "tp", label: "✓ Реальна загроза" },
  { value: "not_sure", label: "? Не впевнений" },
  { value: "comment", label: "💬 Коментар" },
] as const;

type FeedbackLabel = (typeof FEEDBACK_LABELS)[number]["value"];

interface SnapshotDialogProps {
  camera: CameraType;
  onClose: () => void;
}

function SnapshotDialog({ camera, onClose }: SnapshotDialogProps) {
  const [label, setLabel] = useState<FeedbackLabel>("fp");
  const [comment, setComment] = useState("");

  const snapshotQuery = useQuery({
    queryKey: ["snapshot", camera.id],
    queryFn: () =>
      api.post<{ frame_url: string | null; camera_id: string; event_id: string | null }>(
        `/api/cv/cameras/${camera.id}/snapshot`,
      ),
    retry: false,
  });

  const feedbackMutation = useMutation({
    mutationFn: () =>
      api.post("/api/feedback", {
        // Use latest event_id so the mining JOIN resolves; fall back to camera.id
        // (mining will skip it, but the label is still stored for audit).
        alert_id: snapshotQuery.data?.event_id ?? camera.id,
        user_label: label,
        tag: comment || undefined,
      }),
    onSuccess: () => {
      toast.success("Відгук збережено");
      onClose();
    },
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Знімок — ${camera.name}`}>
      {snapshotQuery.isLoading && (
        <div className="flex justify-center py-8">
          <Spinner className="h-6 w-6" />
        </div>
      )}

      {snapshotQuery.data?.frame_url ? (
        <img
          src={snapshotQuery.data.frame_url}
          alt="snapshot"
          className="w-full rounded-xl mb-4 border border-[color:var(--border)]"
        />
      ) : (
        <div className="aspect-video bg-[color:var(--bg)] rounded-xl mb-4 flex items-center justify-center text-[color:var(--text-faint)] text-sm">
          Немає знімку
        </div>
      )}

      <p className="text-sm font-medium text-[color:var(--text)] mb-2">Що зображено?</p>
      <div className="space-y-2 mb-4">
        {FEEDBACK_LABELS.map((fb) => (
          <label key={fb.value} className="flex items-center gap-2.5 cursor-pointer group">
            <input
              type="radio"
              name="feedback"
              value={fb.value}
              checked={label === fb.value}
              onChange={() => setLabel(fb.value)}
              className="accent-primary-500"
            />
            <span className="text-sm text-[color:var(--text)] group-hover:text-[color:var(--text)]">
              {fb.label}
            </span>
          </label>
        ))}
      </div>

      {label === "comment" && (
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Коментар…"
          rows={2}
          className="w-full rounded-xl border border-[color:var(--border)] bg-[color:var(--raised)] px-3 py-2 text-sm text-[color:var(--text)] mb-4 focus:outline-none focus:ring-2 focus:ring-primary-500 resize-none"
        />
      )}

      <div className="flex gap-2 justify-end">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Скасувати
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={feedbackMutation.isPending}
          onClick={() => feedbackMutation.mutate()}
        >
          {feedbackMutation.isPending ? "Зберігаємо…" : "Відправити відгук"}
        </Button>
      </div>
    </Dialog>
  );
}

export default function CamerasPage() {
  const { data: cameras, isLoading } = useQuery<CameraType[]>({
    queryKey: ["cameras"],
    queryFn: () => api.get<CameraType[]>("/api/cv/cameras"),
    staleTime: 30_000,
  });
  const [overlayEnabled, setOverlayEnabled] = useState(true);
  const [privacyMode, setPrivacyMode] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [snapshotCamera, setSnapshotCamera] = useState<CameraType | null>(null);
  const [annotationFrame, setAnnotationFrame] = useState<string | null>(null);
  const [enrollmentsOpen, setEnrollmentsOpen] = useState(false);
  const cameraLiveRef = useRef<CameraLiveHandle>(null);

  const handleAnnotate = () => {
    const dataUrl = cameraLiveRef.current?.capture();
    if (dataUrl) {
      setAnnotationFrame(dataUrl);
    } else {
      toast.error("Не вдалося захопити кадр — відео ще не завантажено");
    }
  };

  if (isLoading) {
    return (
      <div className="flex justify-center pt-16">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  if (!cameras?.length) {
    return (
      <EmptyState
        Icon={Camera}
        message="Немає камер"
        description="Додай камери у план будинку"
      />
    );
  }

  const selected = cameras.find((c) => c.id === selectedId) ?? cameras[0];

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-2xl font-bold text-[color:var(--text)]">Камери</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPrivacyMode((v) => !v)}
            title="Приватний режим"
            className={`p-2 rounded-xl border transition-all ${
              privacyMode
                ? "border-primary-500/40 bg-primary-500/10 text-primary-400"
                : "border-[color:var(--border)] text-[color:var(--text-muted)] hover:bg-[color:var(--raised)]"
            }`}
          >
            {privacyMode ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
          <button
            onClick={() => setOverlayEnabled((v) => !v)}
            title="Overlay детекцій"
            className={`p-2 rounded-xl border transition-all ${
              overlayEnabled
                ? "border-primary-500/40 bg-primary-500/10 text-primary-400"
                : "border-[color:var(--border)] text-[color:var(--text-muted)] hover:bg-[color:var(--raised)]"
            }`}
          >
            <Crosshair size={16} />
          </button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setSnapshotCamera(selected)}
            className="gap-1.5"
          >
            <ImageDown size={14} />
            Знімок
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={handleAnnotate}
            className="gap-1.5"
            disabled={privacyMode}
            title="Захопити кадр і розмітити об'єкти"
          >
            <PenLine size={14} />
            Розмітити
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setEnrollmentsOpen(true)}
            className="gap-1.5"
            title="Знайомі обличчя"
          >
            <Users size={14} />
            Знайомі
          </Button>
        </div>
      </div>

      <CameraLive ref={cameraLiveRef} camera={selected} overlayEnabled={overlayEnabled} blurred={privacyMode} />

      {cameras.length > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {cameras.map((cam) => (
            <button
              key={cam.id}
              onClick={() => setSelectedId(cam.id)}
              className={`shrink-0 flex items-center gap-1.5 text-xs px-3 py-2 rounded-xl border transition-all ${
                cam.id === selected.id
                  ? "border-primary-500/50 bg-primary-500/10 text-primary-300"
                  : "border-[color:var(--border)] text-[color:var(--text-muted)] hover:border-[color:var(--text-faint)]"
              }`}
            >
              <Camera size={13} />
              {cam.name}
              {!cam.online && <span className="h-1.5 w-1.5 rounded-full bg-red-500 ml-0.5" />}
            </button>
          ))}
        </div>
      )}

      {snapshotCamera && (
        <SnapshotDialog camera={snapshotCamera} onClose={() => setSnapshotCamera(null)} />
      )}

      {annotationFrame && (
        <AnnotationModal
          imageDataUrl={annotationFrame}
          onClose={() => setAnnotationFrame(null)}
        />
      )}

      {enrollmentsOpen && (
        <EnrollmentsDialog onClose={() => setEnrollmentsOpen(false)} />
      )}
    </div>
  );
}
