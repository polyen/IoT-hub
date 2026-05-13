import { useConfirmCount } from "../../features/confirm/useConfirmStream";

export function ConfirmBadge() {
  const count = useConfirmCount();
  if (!count) return null;
  return (
    <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-red-600 text-[10px] font-bold text-white px-1 pointer-events-none">
      {count > 9 ? "9+" : count}
    </span>
  );
}
