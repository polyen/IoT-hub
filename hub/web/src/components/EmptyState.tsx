import { LucideIcon } from "lucide-react";

interface EmptyStateProps {
  message: string;
  icon?: string;
  Icon?: LucideIcon;
  description?: string;
}

export function EmptyState({ message, icon = "○", Icon, description }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 animate-fade-in">
      {Icon ? (
        <div className="w-14 h-14 rounded-2xl bg-[color:var(--raised)] flex items-center justify-center">
          <Icon size={26} strokeWidth={1.5} className="text-[color:var(--text-faint)]" />
        </div>
      ) : (
        <span className="text-4xl opacity-30">{icon}</span>
      )}
      <div className="text-center">
        <p className="text-sm font-medium text-[color:var(--text-muted)]">{message}</p>
        {description && (
          <p className="text-xs text-[color:var(--text-faint)] mt-1">{description}</p>
        )}
      </div>
    </div>
  );
}
