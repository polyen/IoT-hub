import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Spinner } from "../../components/Spinner";
import { relativeTime } from "../../lib/format";

const KINDS = ["yolo", "pose", "face", "whisper"] as const;
type Kind = (typeof KINDS)[number];

interface DeployRecord {
  version: string;
  kind: string;
  promoted_at: string;
  rolled_back?: boolean;
}

interface DeployStatus {
  kind: Kind;
  current: string | null;
  available: string[];
  history: DeployRecord[];
}

const KIND_LABEL: Record<Kind, string> = {
  yolo: "YOLO26n det",
  pose: "YOLO26n-pose",
  face: "ArcFace",
  whisper: "Whisper",
};

function TokenGate({
  token,
  onChange,
}: {
  token: string;
  onChange: (t: string) => void;
}) {
  const [editing, setEditing] = useState(!token);
  const [draft, setDraft] = useState("");

  if (!editing && token) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-2.5 text-sm">
        <span className="text-green-400 text-base">●</span>
        <span className="text-slate-300">Deploy token встановлено</span>
        <button
          onClick={() => setEditing(true)}
          className="ml-auto text-xs text-slate-500 hover:text-slate-300"
        >
          Змінити
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-amber-700/60 bg-amber-900/20 px-4 py-3 space-y-2">
      <p className="text-sm text-amber-300">
        Введіть deploy token для управління моделями
      </p>
      <div className="flex gap-2">
        <input
          type="password"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft) {
              onChange(draft);
              sessionStorage.setItem("deploy_token", draft);
              setEditing(false);
              setDraft("");
            }
          }}
          placeholder="Токен розгортання"
          className="flex-1 rounded bg-slate-900 border border-slate-600 px-3 py-1.5 text-sm font-mono focus:outline-none focus:border-blue-500"
        />
        <button
          disabled={!draft}
          onClick={() => {
            onChange(draft);
            sessionStorage.setItem("deploy_token", draft);
            setEditing(false);
            setDraft("");
          }}
          className="px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-sm font-medium"
        >
          Зберегти
        </button>
      </div>
    </div>
  );
}

async function deployFetch<T>(
  url: string,
  token: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Deploy-Token": token,
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error((err as { detail: string }).detail);
  }
  return res.json() as Promise<T>;
}

function KindCard({ kind, token }: { kind: Kind; token: string }) {
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery<DeployStatus>({
    queryKey: ["deploy-status", kind],
    queryFn: () =>
      fetch(`/api/deploy/${kind}/status`).then((r) => r.json()) as Promise<DeployStatus>,
    refetchInterval: 30_000,
  });

  const [selectedVersion, setSelectedVersion] = useState<string>("");

  useEffect(() => {
    if (data?.available?.[0] && !selectedVersion) {
      setSelectedVersion(data.available[0]);
    }
  }, [data, selectedVersion]);

  const promote = useMutation({
    mutationFn: (version: string) =>
      deployFetch(`/api/deploy/${kind}/promote/${encodeURIComponent(version)}`, token, {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["deploy-status", kind] });
    },
  });

  const rollback = useMutation({
    mutationFn: () =>
      deployFetch(`/api/deploy/${kind}/rollback`, token, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["deploy-status", kind] });
    },
  });

  const busy = promote.isPending || rollback.isPending;

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
        <div>
          <span className="font-medium text-sm">{KIND_LABEL[kind]}</span>
          <span className="ml-2 text-xs text-slate-500 font-mono">{kind}</span>
        </div>
        {isLoading && <Spinner className="h-4 w-4" />}
        {data?.current && (
          <span className="text-xs font-mono bg-blue-900/40 text-blue-300 border border-blue-800 px-2 py-0.5 rounded">
            {data.current}
          </span>
        )}
        {!isLoading && !data?.current && (
          <span className="text-xs text-slate-500">немає активної</span>
        )}
      </div>

      {error && (
        <p className="px-4 py-3 text-xs text-red-400">
          {(error as Error).message}
        </p>
      )}

      {(promote.isError || rollback.isError) && (
        <p className="px-4 py-2 text-xs text-red-400 border-b border-slate-700">
          {((promote.error ?? rollback.error) as Error).message}
        </p>
      )}

      {(promote.isSuccess || rollback.isSuccess) && (
        <p className="px-4 py-2 text-xs text-green-400 border-b border-slate-700">
          {promote.isSuccess ? "Промоція успішна" : "Відкат успішний"}
        </p>
      )}

      {/* Controls */}
      <div className="px-4 py-3 space-y-3">
        <div className="flex gap-2">
          <select
            value={selectedVersion}
            onChange={(e) => setSelectedVersion(e.target.value)}
            disabled={!token || !data?.available?.length || busy}
            className="flex-1 rounded bg-slate-900 border border-slate-600 px-2 py-1.5 text-sm font-mono focus:outline-none focus:border-blue-500 disabled:opacity-50"
          >
            {!data?.available?.length && (
              <option value="">— немає версій —</option>
            )}
            {data?.available?.map((v) => (
              <option key={v} value={v}>
                {v}
                {v === data.current ? " (active)" : ""}
              </option>
            ))}
          </select>

          <button
            disabled={!token || !selectedVersion || selectedVersion === data?.current || busy}
            onClick={() => promote.mutate(selectedVersion)}
            className="px-3 py-1.5 rounded bg-green-700 hover:bg-green-600 disabled:opacity-40 text-sm font-medium flex items-center gap-1.5"
          >
            {promote.isPending && <Spinner className="h-3.5 w-3.5" />}
            Promote
          </button>

          <button
            disabled={!token || !data?.current || busy}
            onClick={() => {
              if (confirm(`Відкатити ${kind}? Буде завантажена попередня версія.`)) {
                rollback.mutate();
              }
            }}
            className="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-sm font-medium flex items-center gap-1.5"
          >
            {rollback.isPending && <Spinner className="h-3.5 w-3.5" />}
            Rollback
          </button>
        </div>
      </div>

      {/* History tail */}
      {data?.history && data.history.length > 0 && (
        <div className="border-t border-slate-700">
          <p className="px-4 py-1.5 text-xs text-slate-500 font-semibold uppercase tracking-wider">
            Останні деплої
          </p>
          <div className="divide-y divide-slate-700/60">
            {data.history.slice(-5).reverse().map((rec, i) => (
              <div key={i} className="flex items-center justify-between px-4 py-2 text-xs">
                <span
                  className={`font-mono ${rec.rolled_back ? "line-through text-slate-600" : rec.version === data.current ? "text-blue-400 font-semibold" : "text-slate-400"}`}
                >
                  {rec.version}
                </span>
                <span className="text-slate-600">
                  {relativeTime(rec.promoted_at)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ModelsPage() {
  const [token, setToken] = useState(
    () => sessionStorage.getItem("deploy_token") ?? "",
  );

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Управління моделями</h1>

      <TokenGate token={token} onChange={setToken} />

      {!token && (
        <p className="text-sm text-slate-500 text-center py-4">
          Введіть deploy token щоб бачити доступні версії та керувати деплоєм
        </p>
      )}

      <div className="space-y-4">
        {KINDS.map((kind) => (
          <KindCard key={kind} kind={kind} token={token} />
        ))}
      </div>
    </div>
  );
}
