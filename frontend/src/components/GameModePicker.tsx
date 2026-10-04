import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import ModeOptions from "./ModeOptions";
import { cn } from "../lib/cn";
import { gameModeStyle } from "../lib/gameModes";
import { activeModifierCount, clearModifiers, supportsModifiers } from "../lib/modifiers";
import type { EngineMeta, RulesConfig } from "../types/api";

// Cast-graph classics first, then the rule-based modes, then the trackers.
const MODE_ORDER = [
  "cinechain",
  "auteur_relay",
  "canon_island",
  "meet_in_the_middle",
  "tug_of_war",
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

/** A visual grid of game modes: icon, accent colour, tagline and description per card.
 * Each card hides an expandable "Customize Modifiers" drawer; touching it selects that mode. */
export default function GameModePicker({
  engines,
  value,
  onChange,
  rules,
  onRulesChange,
}: {
  engines: EngineMeta[] | undefined;
  value: string;
  onChange: (gameType: string) => void;
  rules: RulesConfig;
  /** A modifier was changed on `gameType`'s card (which is now the selected mode). */
  onRulesChange: (gameType: string, rules: RulesConfig) => void;
}) {
  const [openDrawer, setOpenDrawer] = useState<string | null>(null);
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
        const capabilities = engines ? mode.capabilities : undefined;
        const customizable = supportsModifiers(mode.game_type, capabilities);
        const modeRules = selected ? rules : clearModifiers(rules);
        const activeCount = selected ? activeModifierCount(mode.game_type, rules) : 0;
        const drawerOpen = customizable && openDrawer === mode.game_type;
        return (
          <div
            key={mode.game_type}
            className={cn(
              "relative flex flex-col overflow-hidden rounded-xl border bg-app-bg text-left transition-all",
              drawerOpen && "sm:col-span-2 lg:col-span-3",
              selected
                ? cn("bg-app-surface", style.ring)
                : "border-app-border hover:-translate-y-0.5 hover:border-zinc-600",
            )}
          >
            <button
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(mode.game_type)}
              className="group flex flex-1 flex-col gap-2 p-3.5 text-left"
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

            {customizable && (
              <div className="border-t border-app-border/70">
                <button
                  type="button"
                  aria-expanded={drawerOpen}
                  onClick={() => setOpenDrawer(drawerOpen ? null : mode.game_type)}
                  className="flex w-full items-center justify-between gap-2 px-3.5 py-2 text-left text-[11px] font-medium text-zinc-400 transition-colors hover:bg-app-surface-hover hover:text-zinc-200"
                >
                  <span className="flex items-center gap-1.5">
                    <span aria-hidden>⚙️</span>
                    Customize Modifiers
                    {activeCount > 0 && (
                      <span className={cn("rounded-full bg-app-surface-hover px-1.5 py-0.5 text-[10px]", style.text)}>
                        {activeCount} on
                      </span>
                    )}
                  </span>
                  <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", drawerOpen && "rotate-180")} />
                </button>
                {drawerOpen && (
                  <div className="px-3 pb-3">
                    <ModeOptions
                      gameType={mode.game_type}
                      value={modeRules}
                      capabilities={capabilities}
                      onChange={(next) => onRulesChange(mode.game_type, next)}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
