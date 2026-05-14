import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { CameraLive } from "./CameraLive";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import type { Camera } from "../../lib/types";

const FEEDBACK_LABELS = [
  { value: "stranger", label: "Незнайомець" },
  { value: "known", label: "Знайома людина" },
  { value: "false_positive", label: "Хибна тривога" },
  { value: "comment", label: "Коментар" },
] as const;

type FeedbackLabel = (typeof FEEDBACK_LABELS)[number]["value"];

interface SnapshotDialogProps {
  camera: Camera;
  onClose: () => void;
}

function SnapshotDialog({ camera, onClose }: SnapshotDialogProps) {
  const [label, setLabel] = useState<FeedbackLabel>("false_positive");
  const [comment, setComment] = useState("");

  const snapshotQuery = useQuery({
    queryKey: ["snapshot", camera.id],
    queryFn: () => api.post<{ frame_url: string | null; camera_id: string }>(
      `/api/cv/cameras/${camera.id}/snapshot`,
    ),
    retry: false,
  });

  const feedbackMutation = useMutation({
    mutationFn: () =>
      api.post("/api/feedback", {
        alert_id: "00000000-0000-0000-0000-000000000000",
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
          className="w-full rounded-lg mb-4 border border-slate-700"
        />
      ) : (
        <div className="aspect-video bg-slate-900 rounded-lg mb-4 flex items-center justify-center text-slate-600 text-sm">
          Немає знімку
        </div>
      )}

      <p className="text-sm font-medium mb-2">Що зображено?</p>
      <div className="space-y-2 mb-4">
        {FEEDBACK_LABELS.map((fb) => (
          <label key={fb.value} className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="feedback"
              value={fb.value}
              checked={label === fb.value}
              onChange={() => setLabel(fb.value)}
              className="accent-blue-500"
            />
            <span className="text-sm">{fb.label}</span>
          </label>
        ))}
      </div>

      {label === "comment" && (
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Коментар…"
          rows={2}
          className="w-full rounded border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white mb-4 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
        />
      )}

      <div className="flex gap-2 justify-end">
        <Button variant="secondary" size="sm" onClick={onClose}>
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
  const { data: cameras, isLoading } = useQuery<Camera[]>({
    queryKey: ["cameras"],
    queryFn: () => api.get<Camera[]>("/api/cv/cameras"),
    staleTime: 30_000,
  });
  const [overlayEnabled, setOverlayEnabled] = useState(true);
  const [privacyMode, setPrivacyMode] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [snapshotCamera, setSnapshotCamera] = useState<Camera | null>(null);

  if (isLoading) {
    return (
      <div className="flex justify-center pt-16">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  if (!cameras?.length) {
    return <EmptyState message="Немає камер. Додай камери у план будинку." icon="⬛" />;
  }

  const selected = cameras.find((c) => c.id === selectedId) ?? cameras[0];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Камери</h1>
        <div className="flex gap-2 flex-wrap">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setPrivacyMode((v) => !v)}
            title="Приватний режим (blur)"
          >
            {privacyMode ? "🔒 Blur" : "👁 Видно"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setOverlayEnabled((v) => !v)}
            title="Overlay детекцій"
          >
            {overlayEnabled ? "🎯 Overlay" : "○ Overlay"}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setSnapshotCamera(selected)}
          >
            📸 Знімок
          </Button>
        </div>
      </div>

      <CameraLive camera={selected} overlayEnabled={overlayEnabled} blurred={privacyMode} />

      {cameras.length > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {cameras.map((cam) => (
            <button
              key={cam.id}
              onClick={() => setSelectedId(cam.id)}
              className={`shrink-0 text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                cam.id === selected.id
                  ? "border-blue-500 bg-blue-600/20 text-blue-300"
                  : "border-slate-700 text-slate-400 hover:border-slate-500"
              }`}
            >
              ⬛ {cam.name}
              {!cam.online && <span className="ml-1 text-red-400">●</span>}
            </button>
          ))}
        </div>
      )}

      {snapshotCamera && (
        <SnapshotDialog camera={snapshotCamera} onClose={() => setSnapshotCamera(null)} />
      )}
    </div>
  );
}
