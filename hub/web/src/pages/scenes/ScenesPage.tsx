import { useState } from "react";
import { Link } from "react-router-dom";
import { Play, Sparkles, ChevronRight } from "lucide-react";
import { SCENES, useRunScene, type Scene } from "../../features/scenes/scenes";
import { Spinner } from "../../components/Spinner";
import { ScenePreviewDialog } from "./ScenePreviewDialog";

export default function ScenesPage() {
  const { run, runningId } = useRunScene();
  const [preview, setPreview] = useState<Scene | null>(null);

  return (
    <div className="space-y-5 animate-fade-in">
      <div className="flex items-center gap-2.5">
        <Sparkles size={22} className="text-primary-400" />
        <h1 className="font-display text-2xl font-semibold text-[color:var(--text)]">Сцени</h1>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {SCENES.map((scene) => {
          const busy = runningId === scene.id;
          return (
            <button
              key={scene.id}
              disabled={busy}
              onClick={() => setPreview(scene)}
              className="card card-hover flex items-center gap-4 rounded-2xl p-4 text-left disabled:opacity-50"
            >
              <span className="select-none text-3xl">{scene.icon}</span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-[color:var(--text)]">{scene.name}</p>
                <p className="mt-0.5 truncate text-xs text-[color:var(--text-muted)]">
                  {scene.description}
                </p>
              </div>
              <span className="shrink-0 text-primary-400">
                {busy ? <Spinner className="h-4 w-4" /> : <Play size={16} />}
              </span>
            </button>
          );
        })}
      </div>

      <Link
        to="/assistant?tab=scenarios"
        className="card card-hover flex items-center gap-3 rounded-2xl px-4 py-3.5"
      >
        <span className="flex-1 text-sm text-[color:var(--text-muted)]">
          Довільні команди та симулятор політики — в Асистенті
        </span>
        <ChevronRight size={15} className="text-[color:var(--text-faint)]" />
      </Link>

      <ScenePreviewDialog
        scene={preview}
        onClose={() => setPreview(null)}
        running={runningId === preview?.id}
        onConfirm={(s) => {
          run(s);
        }}
      />
    </div>
  );
}
