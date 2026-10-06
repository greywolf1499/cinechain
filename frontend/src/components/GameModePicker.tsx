import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import ModeOptions from "./ModeOptions";
import { ModeRulebookHelp } from "./HowToPlay";
import { cn } from "../lib/cn";
import { gameModeStyle, LEGACY_MODE_COPY } from "../lib/gameModes";
import { activeModifierCount, clearModifiers, supportsModifiers } from "../lib/modifiers";
import type { EngineMeta, RulesConfig } from "../types/api";

// Cast-graph classics first, then the rule-based modes, then the trackers.
export const MODE_ORDER = [
  "cinechain",
  "auteur_relay",
  "crew_craft",
  "canon_island",
  "meet_in_the_middle",
  "tug_of_war",
  "rabbit_hole",
  "march_madness",
  "method_actor",
  "auteur_marathon",
  "regional_deep_dive",
  "rt_split",
  "chrono_climb",
  "historical_time_travel",
  "genre_pendulum",
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
  showModifierDrawer = true,
  onAdvance,
}: {
  engines: EngineMeta[] | undefined;
  value: string;
  onChange: (gameType: string) => void;
  rules: RulesConfig;
  /** A modifier was changed on `gameType`'s card (which is now the selected mode). */
  onRulesChange: (gameType: string, rules: RulesConfig) => void;
  /** Hide the legacy inline drawer when modifiers are presented elsewhere. */
  showModifierDrawer?: boolean;
  onAdvance?: (gameType: string) => void;
}) {
  const [openDrawer, setOpenDrawer] = useState<string | null>(null);
  const modes: EngineMeta[] = [
    ...(engines ?? [
      { game_type: "cinechain", display_name: "CineChain", description: "", capabilities: [], seed_policy: "free", discovery_filters: [] },
    ]),
  ].sort((a, b) => orderOf(a.game_type) - orderOf(b.game_type));

  return (
    <div role="radiogroup" aria-label="Game mode" className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
      {modes.map((mode) => {
        const style = gameModeStyle(mode.game_type);
        const copy = LEGACY_MODE_COPY[mode.game_type];
        const tags = mode.tags ?? copy?.tags ?? [];
        const Icon = style.icon;
        const selected = mode.game_type === value;
        const capabilities = engines ? mode.capabilities : undefined;
        const customizable = showModifierDrawer && supportsModifiers(mode.game_type, capabilities);
        const modeRules = selected ? rules : clearModifiers(rules);
        const activeCount = selected ? activeModifierCount(mode.game_type, rules) : 0;
        const drawerOpen = customizable && openDrawer === mode.game_type;
        return (
          <div
            key={mode.game_type}
            className={cn(
              "relative flex flex-col overflow-hidden rounded-xl border bg-app-bg text-left transition-all",
              drawerOpen && "sm:col-span-2 lg:col-span-3",
              mode.unavailable_reason
                ? "border-amber-700/50 opacity-55"
                : selected
                ? cn("bg-app-surface", style.ring)
                : "border-app-border hover:-translate-y-0.5 hover:border-zinc-600",
            )}
          >
            <button
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(mode.game_type)}
              onDoubleClick={() => onAdvance?.(mode.game_type)}
              onKeyDown={(event) => {
                if (event.key === "Enter") onAdvance?.(mode.game_type);
              }}
              className="group flex flex-1 flex-col gap-2 p-3.5 pr-10 text-left"
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
                <p className={cn("text-[11px] font-medium", style.text)}>{mode.tagline ?? copy?.tagline ?? mode.display_name}</p>
              </div>
              {mode.description && (
                <p className="line-clamp-4 text-[11px] leading-relaxed text-zinc-500">{mode.description}</p>
              )}
              {mode.unavailable_reason && (
                <span className="w-fit rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[10px] font-medium text-amber-200">
                  Needs {mode.requires?.join(", ") || "configuration"}
                </span>
              )}
              {copy?.progression && (
                <ol
                  aria-label="Tier progression"
                  className="flex flex-wrap items-center gap-x-1 gap-y-1 text-[10px] font-medium text-zinc-400"
                >
                  {copy.progression.map((step, index) => (
                    <li key={step} className="flex items-center gap-1">
                      {index > 0 && <span aria-hidden className="text-zinc-600">→</span>}
                      <span className={cn("rounded px-1.5 py-0.5 ring-1 ring-inset ring-zinc-700", style.text)}>
                        <span className="mr-1 text-zinc-500">T{index + 1}</span>
                        {step}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
              {tags.length > 0 && (
                <div className="mt-auto flex flex-wrap gap-1 pt-1">
                  {tags.map((tag) => (
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
            <ModeRulebookHelp engine={mode} />

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
