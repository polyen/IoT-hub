import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import { Dialog } from "../../components/Dialog";
import { Spinner } from "../../components/Spinner";
import { Button } from "../../components/Button";

interface Props {
  onClose: () => void;
}

export function EnrollmentsDialog({ onClose }: Props) {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["enrollments"],
    queryFn: () => api.get<{ names: string[]; count: number }>("/api/cv/enrollments"),
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) =>
      api.delete(`/api/cv/enrollments/${encodeURIComponent(name)}`),
    onSuccess: (_d, name) => {
      toast.success(`"${name}" видалено`);
      qc.invalidateQueries({ queryKey: ["enrollments"] });
    },
    onError: () => toast.error("Помилка видалення"),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Знайомі обличчя">
      {isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner className="h-6 w-6" />
        </div>
      ) : !data?.names.length ? (
        <p className="py-8 text-center text-sm text-[color:var(--text-faint)]">
          Ще нікого не додано — клікни на людину в кадрі щоб назвати
        </p>
      ) : (
        <ul className="space-y-2">
          {data.names.map((name) => (
            <li
              key={name}
              className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-[color:var(--raised)]"
            >
              <div className="h-8 w-8 shrink-0 rounded-full bg-primary-500/20 flex items-center justify-center text-primary-400 text-sm font-bold uppercase select-none">
                {name[0]}
              </div>
              <span className="flex-1 text-sm text-[color:var(--text)]">{name}</span>
              <button
                onClick={() => deleteMutation.mutate(name)}
                disabled={deleteMutation.isPending && deleteMutation.variables === name}
                className="p-1.5 rounded-lg text-[color:var(--text-muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-40"
                title={`Видалити ${name}`}
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex justify-end mt-4">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Закрити
        </Button>
      </div>
    </Dialog>
  );
}
