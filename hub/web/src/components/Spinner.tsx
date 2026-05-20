import { clsx } from "clsx";

export function Spinner({ className }: { className?: string }) {
  return (
    <div
      className={clsx(
        "inline-block rounded-full border-2 border-[color:var(--border)] border-t-primary-500 animate-spin",
        className ?? "h-5 w-5",
      )}
    />
  );
}
