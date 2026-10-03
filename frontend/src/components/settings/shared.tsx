import type { ReactNode } from "react";

export const inputClass =
  "rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";

export function SettingsCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-app-border bg-app-surface">
      <div className="border-b border-app-border px-5 py-3 text-sm font-medium text-zinc-300">
        {title}
      </div>
      {children}
    </div>
  );
}

export function MiniStat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <p className="text-lg font-semibold text-zinc-100">{value}</p>
      <p className="mt-0.5 text-xs text-zinc-500">{label}</p>
    </div>
  );
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}
