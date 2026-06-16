import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

interface Props {
  /** uPlot options minus width/height — memoize in the parent to avoid rebuilds. */
  options: Omit<uPlot.Options, "width" | "height">;
  data: uPlot.AlignedData;
  height?: number;
}

/**
 * Thin React wrapper around uPlot: builds the chart on mount/options-change,
 * pushes new data without a full rebuild, and tracks container width via a
 * ResizeObserver so the canvas stays responsive in the flex/grid layout.
 */
export function UPlotChart({ options, data, height = 240 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const width = el.clientWidth || 600;
    const u = new uPlot({ ...options, width, height }, data, el);
    plotRef.current = u;

    const ro = new ResizeObserver(() => {
      u.setSize({ width: el.clientWidth || width, height });
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      u.destroy();
      plotRef.current = null;
    };
    // data is intentionally excluded — updates flow through the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, height]);

  useEffect(() => {
    plotRef.current?.setData(data);
  }, [data]);

  return <div ref={containerRef} className="w-full" />;
}
