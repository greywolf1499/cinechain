import { Ban, Gauge } from "lucide-react";
import CountryFlags from "./CountryFlags";
import { countryName } from "../lib/countryNames";
import type { ConstraintInfo } from "../types/api";

/** The run's active modifiers for the next pick: a "Cooldown: [flags]" chip listing the
 * countries temporarily locked out, plus one chip per other active modifier. */
export default function ModifierChips({ constraint }: { constraint: ConstraintInfo | null | undefined }) {
  const locked = constraint?.cooldown_countries ?? [];
  const notes = constraint?.modifier_notes ?? [];
  if (locked.length === 0 && notes.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {locked.length > 0 && (
        <span
          className="inline-flex items-center gap-1.5 rounded-full border border-teal-400/30 bg-teal-500/10 px-2.5 py-1 text-[11px] font-medium text-teal-200"
          title={`Locked out for now: ${locked.map((code) => countryName(code)).join(", ")}`}
        >
          <Ban className="h-3 w-3" />
          Cooldown:
          <span className="flex items-center gap-1 text-sm leading-none">
            <CountryFlags codes={locked} />
          </span>
        </span>
      )}
      {notes.map((note) => (
        <span
          key={note}
          className="inline-flex items-center gap-1 rounded-full border border-app-border bg-app-bg px-2.5 py-1 text-[11px] text-zinc-300"
        >
          <Gauge className="h-3 w-3 text-zinc-500" />
          {note}
        </span>
      ))}
    </div>
  );
}
