import type { ReactNode } from "react";
import type { RulesConfig } from "../../types/api";
import { DEFAULT_TARGET_LEAD } from "../../lib/tugOfWar";

export const inputClass =
  "w-full rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
      {label}
      {children}
    </label>
  );
}

export const LATEST_DECADE = Math.floor(new Date().getFullYear() / 10) * 10;
export const DECADES = Array.from(
  { length: (LATEST_DECADE - 1890) / 10 + 1 },
  (_, index) => LATEST_DECADE - index * 10,
);

export const TRACKER_RULES: RulesConfig = {
  preset: "custom",
  allow_repeats: "strict",
  no_consecutive_actor: false,
  max_cast_order: 15,
  min_runtime: 0,
  wildcards_budget: 0,
};

export const DEFAULT_DRAFT_TUG_LEAD = DEFAULT_TARGET_LEAD;
