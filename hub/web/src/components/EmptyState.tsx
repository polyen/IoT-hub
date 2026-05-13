interface EmptyStateProps {
  message: string;
  icon?: string;
}

export function EmptyState({ message, icon = "○" }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 text-slate-500 dark:text-slate-500 light:text-slate-400">
      <span className="text-4xl">{icon}</span>
      <p className="text-sm">{message}</p>
    </div>
  );
}
