import { useCallback, useRef, useState } from "react";

export interface RangeSliderProps {
  value: number;
  min: number;
  max: number;
  step?: number;
  onCommit: (value: number) => void;
  label?: string;
  valueLabel?: (v: number) => string;
  gradient?: string;
  disabled?: boolean;
}

export function RangeSlider({
  value,
  min,
  max,
  step = 1,
  onCommit,
  label,
  valueLabel,
  gradient,
  disabled = false,
}: RangeSliderProps) {
  const [live, setLive] = useState(value);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const pct = `${Math.round(((live - min) / (max - min)) * 100)}%`;

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const next = parseFloat(e.target.value);
      setLive(next);
      // Debounce API call ~300 ms while dragging
      if (debounceRef.current !== null) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        onCommit(next);
        debounceRef.current = null;
      }, 300);
    },
    [onCommit],
  );

  const handleCommitNow = useCallback(
    (e: React.SyntheticEvent<HTMLInputElement>) => {
      // Flush immediately on release so keyboard users get instant response
      if (debounceRef.current !== null) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      const next = parseFloat((e.target as HTMLInputElement).value);
      setLive(next);
      onCommit(next);
    },
    [onCommit],
  );

  const displayed = valueLabel ? valueLabel(live) : String(live);

  return (
    <div className="flex items-center gap-3 w-full">
      <input
        type="range"
        className="range-slider flex-1"
        min={min}
        max={max}
        step={step}
        value={live}
        onChange={handleChange}
        onMouseUp={handleCommitNow}
        onPointerUp={handleCommitNow}
        onKeyUp={handleCommitNow}
        onBlur={handleCommitNow}
        disabled={disabled}
        aria-label={label}
        style={
          {
            "--range-pct": pct,
            "--range-grad": gradient ?? `linear-gradient(90deg, var(--primary), var(--warm))`,
          } as React.CSSProperties
        }
      />
      <span
        className="text-xs font-mono tabular-nums text-[color:var(--text-muted)] w-10 text-right shrink-0"
        aria-hidden="true"
      >
        {displayed}
      </span>
    </div>
  );
}
