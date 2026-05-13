import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { CameraLive } from "./CameraLive";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { Button } from "../../components/Button";
import type { Camera } from "../../lib/types";

export default function CamerasPage() {
  const { data: cameras, isLoading } = useQuery<Camera[]>({
    queryKey: ["cameras"],
    queryFn: () => api.get<Camera[]>("/api/cv/cameras"),
    staleTime: 30_000,
  });
  const [overlayEnabled, setOverlayEnabled] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

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
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Камери</h1>
        <Button size="sm" variant="ghost" onClick={() => setOverlayEnabled((v) => !v)}>
          {overlayEnabled ? "👁 Overlay вкл" : "👁 Overlay викл"}
        </Button>
      </div>

      {/* Main stream */}
      <CameraLive camera={selected} overlayEnabled={overlayEnabled} />

      {/* Camera selector thumbnails */}
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
    </div>
  );
}
