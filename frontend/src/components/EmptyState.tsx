import type { LucideIcon } from "lucide-react";

export default function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: { label: string; onClick: () => void; icon?: LucideIcon };
}) {
  const ActionIcon = action?.icon;

  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-app-border bg-app-surface/40 px-6 py-16 text-center">
      <Icon className="h-8 w-8 text-zinc-600" />
      <h2 className="text-sm font-medium text-zinc-300">{title}</h2>
      <p className="max-w-sm text-sm text-zinc-500">{description}</p>
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="inline-flex min-h-10 items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-app-surface"
        >
          {ActionIcon && <ActionIcon className="h-4 w-4" />}
          {action.label}
        </button>
      )}
    </div>
  );
}
