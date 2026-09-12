import type { LucideIcon } from "lucide-react";

export default function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-app-border bg-app-surface/40 px-6 py-16 text-center">
      <Icon className="h-8 w-8 text-zinc-600" />
      <h2 className="text-sm font-medium text-zinc-300">{title}</h2>
      <p className="max-w-sm text-sm text-zinc-500">{description}</p>
    </div>
  );
}
