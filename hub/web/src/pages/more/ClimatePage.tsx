import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import uPlot from "uplot";
import { Thermometer, Droplets, Sun, Zap } from "lucide-react";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { UPlotChart } from "../../components/UPlotChart";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { api } from "../../lib/api";

// ── API types ──────────────────────────────────────────────────────────────
interface RoomClimate {
  room: string;
  ts: string | null;
  values: Record<string, number>;
}
interface LatestOut {
  rooms: Record<string, RoomClimate>;
}
interface TsPoint {
  t: string;
  values: Record<string, number>;
}
interface TimeseriesOut {
  room: string;
  bucket: string;
  fields: string[];
  points: TsPoint[];
}

// ── Field catalogue ──────────────────────────────────────────────────────────
interface FieldMeta {
  label: string;
  unit: string;
  color: string;
  digits: number;
}
const FIELDS: Record<string, FieldMeta> = {
  temperature: { label: "Температура", unit: "°C", color: "#fb923c", digits: 1 },
  humidity: { label: "Вологість", unit: "%", color: "#38bdf8", digits: 1 },
  illuminance: { label: "Освітленість", unit: "lx", color: "#facc15", digits: 0 },
  power_w: { label: "Потужність", unit: "Вт", color: "#a78bfa", digits: 0 },
  voltage_v: { label: "Напруга", unit: "В", color: "#34d399", digits: 1 },
  current_a: { label: "Струм", unit: "А", color: "#f472b6", digits: 2 },
};
const ALL_FIELDS = Object.keys(FIELDS);

const RANGES = ["1h", "6h", "24h", "7d"] as const;
type Range = (typeof RANGES)[number];
const RANGE_LABEL: Record<Range, string> = {
  "1h": "1 год", "6h": "6 год", "24h": "24 год", "7d": "7 днів",
};

const AXIS = "#5b6677";
const GRID = "rgba(120,130,150,0.12)";

function mkSeries(field: string, scale: string): uPlot.Series {
  const m = FIELDS[field];
  return {
    label: m.label,
    scale,
    stroke: m.color,
    width: 2,
    points: { show: false },
    value: (_u, v) => (v == null ? "—" : `${v.toFixed(m.digits)} ${m.unit}`),
  };
}

function darkAxis(scale: string, side: 1 | 3, showGrid: boolean): uPlot.Axis {
  return {
    scale,
    side,
    stroke: AXIS,
    grid: { show: showGrid, stroke: GRID, width: 1 },
    ticks: { show: false },
    font: "11px ui-monospace, monospace",
  };
}

// ── Dual-axis time chart ─────────────────────────────────────────────────────
function TimeChart({
  points,
  left,
  right,
  height = 220,
}: {
  points: TsPoint[];
  left: string[];
  right: string[];
  height?: number;
}) {
  const fields = useMemo(() => [...left, ...right], [left, right]);
  const fieldsKey = fields.join(",");

  const data = useMemo<uPlot.AlignedData>(() => {
    const xs = points.map((p) => Date.parse(p.t) / 1000);
    const series = fields.map((f) => points.map((p) => p.values[f] ?? null));
    return [xs, ...series];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, fieldsKey]);

  const options = useMemo<Omit<uPlot.Options, "width" | "height">>(() => {
    const series: uPlot.Series[] = [{}];
    const scales: NonNullable<uPlot.Options["scales"]> = { x: { time: true } };
    const axes: uPlot.Axis[] = [
      { stroke: AXIS, grid: { show: true, stroke: GRID, width: 1 }, ticks: { show: false }, font: "11px ui-monospace, monospace" },
    ];
    if (left.length) {
      scales.L = {};
      axes.push(darkAxis("L", 3, true));
      for (const f of left) series.push(mkSeries(f, "L"));
    }
    if (right.length) {
      scales.R = {};
      axes.push(darkAxis("R", 1, false));
      for (const f of right) series.push(mkSeries(f, "R"));
    }
    return {
      scales,
      series,
      axes,
      legend: { live: true },
      cursor: { points: { size: 6 } },
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldsKey]);

  const hasData = points.some((p) => fields.some((f) => p.values[f] != null));
  if (!hasData) {
    return <p className="py-10 text-center text-sm text-[color:var(--text-muted)]">Немає даних за період</p>;
  }
  return <UPlotChart options={options} data={data} height={height} />;
}

// ── Correlation scatter (x vs y) ─────────────────────────────────────────────
function ScatterChart({
  points,
  xField,
  yField,
  height = 240,
}: {
  points: TsPoint[];
  xField: string;
  yField: string;
  height?: number;
}) {
  const data = useMemo<uPlot.AlignedData>(() => {
    const pairs: [number, number][] = [];
    for (const p of points) {
      const x = p.values[xField];
      const y = p.values[yField];
      if (x != null && y != null) pairs.push([x, y]);
    }
    // uPlot assumes a monotonically increasing x — sort the cloud by x so the
    // point renderer and cursor behave with our non-time x axis.
    pairs.sort((a, b) => a[0] - b[0]);
    return [pairs.map((p) => p[0]), pairs.map((p) => p[1])];
  }, [points, xField, yField]);

  const xm = FIELDS[xField];
  const ym = FIELDS[yField];
  const options = useMemo<Omit<uPlot.Options, "width" | "height">>(
    () => ({
      scales: { x: { time: false }, y: {} },
      series: [
        { label: xm.label },
        {
          label: ym.label,
          stroke: ym.color,
          fill: `${ym.color}55`,
          paths: () => null,
          points: { show: true, size: 7, fill: ym.color, stroke: ym.color },
          value: (_u, v) => (v == null ? "—" : `${v.toFixed(ym.digits)} ${ym.unit}`),
        },
      ],
      axes: [
        {
          stroke: AXIS,
          grid: { show: true, stroke: GRID, width: 1 },
          ticks: { show: false },
          font: "11px ui-monospace, monospace",
          values: (_u, splits) => splits.map((s) => `${s.toFixed(xm.digits)}${xm.unit}`),
        },
        darkAxis("y", 3, true),
      ],
      legend: { show: false },
      cursor: { drag: { x: false, y: false } },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [xField, yField],
  );

  if (data[0].length < 2) {
    return <p className="py-10 text-center text-sm text-[color:var(--text-muted)]">Замало точок для кореляції</p>;
  }
  return <UPlotChart options={options} data={data} height={height} />;
}

// ── Card shell ───────────────────────────────────────────────────────────────
function ChartCard({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="card rounded-2xl p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[color:var(--text-muted)]">{icon}</span>
        <h2 className="text-sm font-semibold text-[color:var(--text)]">{title}</h2>
      </div>
      {children}
    </div>
  );
}

function LatestChips({ climate }: { climate: RoomClimate | undefined }) {
  if (!climate) return null;
  const order = ["temperature", "humidity", "illuminance", "power_w", "voltage_v", "current_a"];
  const present = order.filter((f) => climate.values[f] != null);
  if (present.length === 0) {
    return <p className="text-sm text-[color:var(--text-muted)]">Немає свіжих показників для цієї кімнати.</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {present.map((f) => {
        const m = FIELDS[f];
        return (
          <div
            key={f}
            className="flex items-baseline gap-1.5 rounded-xl border border-[color:var(--border)] bg-[color:var(--raised)] px-3 py-2"
          >
            <span className="font-mono text-lg font-bold tabular-nums" style={{ color: m.color }}>
              {climate.values[f].toFixed(m.digits)}
            </span>
            <span className="text-xs text-[color:var(--text-muted)]">{m.unit}</span>
            <span className="ml-1 text-[10px] uppercase tracking-wide text-[color:var(--text-faint)]">{m.label}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function ClimatePage() {
  const { data: plan, isLoading: planLoading } = useFloorPlan();
  const [range, setRange] = useState<Range>("24h");
  const [room, setRoom] = useState<string | null>(null);

  const rooms = plan?.rooms ?? [];
  const selected = room ?? rooms[0]?.slug ?? null;

  const { data: latest } = useQuery<LatestOut>({
    queryKey: ["sensors-latest"],
    queryFn: () => api.get<LatestOut>("/api/sensors/latest", true),
    refetchInterval: 30_000,
    staleTime: 25_000,
  });

  const { data: ts, isLoading: tsLoading } = useQuery<TimeseriesOut>({
    queryKey: ["sensors-ts", selected, range],
    queryFn: () =>
      api.get<TimeseriesOut>(
        `/api/sensors/timeseries?room=${encodeURIComponent(selected!)}&fields=${ALL_FIELDS.join(",")}&range=${range}`,
        true,
      ),
    enabled: !!selected,
    refetchInterval: 60_000,
  });

  const points = ts?.points ?? [];
  const fieldHasData = (f: string) => points.some((p) => p.values[f] != null);
  const hasPower = ["power_w", "voltage_v", "current_a"].some(fieldHasData);
  const hasLux = fieldHasData("illuminance");
  const hasCorr = fieldHasData("temperature") && fieldHasData("humidity");

  if (planLoading) {
    return (
      <div className="flex justify-center pt-16">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }
  if (rooms.length === 0) {
    return <EmptyState message="Спочатку створіть план будинку з кімнатами" icon="⌂" />;
  }

  return (
    <div className="space-y-5 animate-fade-in">
      <div>
        <h1 className="font-display text-2xl font-semibold text-[color:var(--text)]">Мікроклімат</h1>
        <p className="mt-1 text-xs text-[color:var(--text-muted)]">
          Динаміка показників сенсорів по кімнатах
        </p>
      </div>

      {/* Room selector */}
      <div className="flex flex-wrap gap-1.5">
        {rooms.map((r) => (
          <button
            key={r.slug}
            onClick={() => setRoom(r.slug)}
            className={[
              "rounded-lg border px-3 py-1.5 text-xs transition-all",
              selected === r.slug
                ? "border-primary-500/40 bg-primary-600/20 text-primary-300"
                : "border-[color:var(--border)] text-[color:var(--text-muted)] hover:bg-[color:var(--raised)]",
            ].join(" ")}
          >
            {r.name}
          </button>
        ))}
      </div>

      {/* Range selector */}
      <div className="flex gap-1.5">
        {RANGES.map((rg) => (
          <button
            key={rg}
            onClick={() => setRange(rg)}
            className={[
              "rounded-lg px-3 py-1.5 text-xs font-medium transition-all",
              range === rg
                ? "bg-[color:var(--text)] text-[color:var(--bg)]"
                : "bg-[color:var(--raised)] text-[color:var(--text-muted)] hover:text-[color:var(--text)]",
            ].join(" ")}
          >
            {RANGE_LABEL[rg]}
          </button>
        ))}
      </div>

      <LatestChips climate={selected ? latest?.rooms[selected] : undefined} />

      {tsLoading ? (
        <div className="flex justify-center pt-10">
          <Spinner className="h-7 w-7" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ChartCard title="Температура та вологість" icon={<Thermometer size={16} />}>
            <TimeChart points={points} left={["temperature"]} right={["humidity"]} />
          </ChartCard>

          {hasCorr && (
            <ChartCard title="Кореляція: температура ↔ вологість" icon={<Droplets size={16} />}>
              <ScatterChart points={points} xField="temperature" yField="humidity" />
            </ChartCard>
          )}

          {hasLux && (
            <ChartCard title="Освітленість" icon={<Sun size={16} />}>
              <TimeChart points={points} left={["illuminance"]} right={[]} />
            </ChartCard>
          )}

          {hasPower && (
            <ChartCard title="Енергоспоживання" icon={<Zap size={16} />}>
              <TimeChart points={points} left={["power_w"]} right={["current_a"]} />
            </ChartCard>
          )}
        </div>
      )}
    </div>
  );
}
