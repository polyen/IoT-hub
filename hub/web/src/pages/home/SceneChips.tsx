import { SCENES, useRunScene } from "../../features/scenes/scenes";
import { Spinner } from "../../components/Spinner";

export function SceneChips() {
  const { run, runningId } = useRunScene();
  const quick = SCENES.filter((s) => s.quick);

  return (
    <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none]">
      {quick.map((scene) => {
        const busy = runningId === scene.id;
        return (
          <button
            key={scene.id}
            disabled={busy}
            onClick={() => run(scene)}
            className="card card-hover flex shrink-0 items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium text-[color:var(--text)] disabled:opacity-50"
          >
            {busy ? (
              <Spinner className="h-4 w-4" />
            ) : (
              <span className="text-lg leading-none">{scene.icon}</span>
            )}
            {scene.short ?? scene.name}
          </button>
        );
      })}
    </div>
  );
}
