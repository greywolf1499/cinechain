import { Check } from "lucide-react";
import { cn } from "../lib/cn";
import { gameModeStyle } from "../lib/gameModes";
import type { EngineMeta } from "../types/api";

// Cast-graph classics first, then the rule-based modes, then the trackers.
const MODE_ORDER = [
  "cinechain",
  "auteur_relay",
  "canon_island",
  "chrono_climb",
  "world_passport",
  "aesthetic_gradient",
  "semantic_trope",
  "decade_sieve",
  "roulette",
];

function orderOf(gameType: string): number {
  const index = MODE_ORDER.indexOf(gameType);
  return index === -1 ? MODE_ORDER.length : index;
}

/** A visual grid of game modes: icon, accent colour, tagline and description per card. */
export default function GameModePicker({
  engines,
  value,
  onChange,
}: {
  engines: EngineMeta[] | undefined;
  value: string;
  onChange: (gameType: string) => void;
}) {
  const modes = [
    ...(engines ?? [
      { game_type: "cinechain", display_name: "CineChain", description: "", capabilities: [] },
    ]),
  ].sort((a, b) => orderOf(a.game_type) - orderOf(b.game_type));

  return (
    <div role="radiogroup" aria-label="Game mode" className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
      {modes.map((mode) => {
        const style = gameModeStyle(mode.game_type);
        const Icon = style.icon;
        const selected = mode.game_type === value;
        return (
          <button
            key={mode.game_type}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(mode.game_type)}
            className={cn(
              "group relative flex flex-col gap-2 overflow-hidden rounded-xl border bg-app-bg p-3.5 text-left transition-all",
              selected
                ? cn("bg-app-surface", style.ring)
                : "border-app-border hover:-translate-y-0.5 hover:border-zinc-600",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <span className={cn("flex h-10 w-10 items-center justify-center rounded-xl", style.bubble)}>
                <Icon className="h-5 w-5" />
              </span>
              {selected && (
                <span className={cn("flex h-5 w-5 items-center justify-center rounded-full bg-current", style.text)}>
                  <Check className="h-3 w-3 text-zinc-950" strokeWidth={3} />
                </span>
              )}
            </div>
            <div>
              <p className="text-sm font-semibold text-zinc-100">{mode.display_name}</p>
              <p className={cn("text-[11px] font-medium", style.text)}>{style.tagline}</p>
            </div>
            {mode.description && (
              <p className="line-clamp-4 text-[11px] leading-relaxed text-zinc-500">{mode.description}</p>
            )}
            {style.tags.length > 0 && (
              <div className="mt-auto flex flex-wrap gap-1 pt-1">
                {style.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full bg-app-surface-hover px-2 py-0.5 text-[10px] font-medium text-zinc-400"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
