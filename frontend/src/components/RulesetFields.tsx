import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "../lib/cn";
import type { RepeatPolicy, RulesConfig } from "../types/api";

export const RULE_PRESETS: Record<"standard" | "purist" | "casual", RulesConfig> = {
  standard: {
    preset: "standard",
    allow_repeats: "strict",
    no_consecutive_actor: true,
    max_cast_order: 15,
    min_runtime: 40,
    wildcards_budget: 2,
  },
  purist: {
    preset: "purist",
    allow_repeats: "strict",
    no_consecutive_actor: true,
    max_cast_order: 5,
    min_runtime: 60,
    wildcards_budget: 0,
  },
  casual: {
    preset: "casual",
    allow_repeats: "penalty",
    no_consecutive_actor: false,
    max_cast_order: 25,
    min_runtime: 0,
    wildcards_budget: -1,
  },
};

export default function RulesetFields({
  value,
  onChange,
}: {
  value: RulesConfig;
  onChange: (rules: RulesConfig) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  function update<K extends keyof RulesConfig>(key: K, val: RulesConfig[K]) {
    onChange({ ...value, [key]: val, preset: "custom" });
  }

  return (
    <div className="rounded-md border border-app-border">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
      >
        <span className="text-xs font-medium text-zinc-400">
          Ruleset <span className="capitalize text-zinc-300">({value.preset})</span>
        </span>
        <ChevronDown
          className={cn("h-3.5 w-3.5 text-zinc-500 transition-transform", expanded && "rotate-180")}
        />
      </button>

      <div className="flex flex-wrap gap-1.5 px-3 pb-2.5">
        {(Object.keys(RULE_PRESETS) as (keyof typeof RULE_PRESETS)[]).map((name) => (
          <PresetPill
            key={name}
            active={value.preset === name}
            onClick={() => onChange(RULE_PRESETS[name])}
          >
            {name[0].toUpperCase() + name.slice(1)}
          </PresetPill>
        ))}
        <PresetPill active={value.preset === "custom"} onClick={() => update("preset", "custom")}>
          Custom
        </PresetPill>
      </div>

      {expanded && (
        <div className="flex flex-col gap-3 border-t border-app-border px-3 py-3">
          <div>
            <p className="mb-1.5 text-xs text-zinc-500">No Repeats</p>
            <div className="flex gap-3">
              {(["strict", "penalty", "allowed"] as RepeatPolicy[]).map((option) => (
                <label
                  key={option}
                  className="flex items-center gap-1.5 text-xs capitalize text-zinc-300"
                >
                  <input
                    type="radio"
                    name="allow_repeats"
                    checked={value.allow_repeats === option}
                    onChange={() => update("allow_repeats", option)}
                    className="accent-accent"
                  />
                  {option}
                </label>
              ))}
            </div>
          </div>

          <label className="flex items-center justify-between text-xs text-zinc-300">
            No consecutive actor reuse
            <input
              type="checkbox"
              checked={value.no_consecutive_actor}
              onChange={(e) => update("no_consecutive_actor", e.target.checked)}
              className="accent-accent"
            />
          </label>

          <label className="flex items-center justify-between text-xs text-zinc-300">
            Max cast depth
            <input
              type="number"
              min={5}
              max={30}
              value={value.max_cast_order}
              onChange={(e) => update("max_cast_order", Number(e.target.value))}
              className={numberInputClass}
            />
          </label>

          <label className="flex items-center justify-between text-xs text-zinc-300">
            Min runtime (minutes, 0 = no limit)
            <input
              type="number"
              min={0}
              value={value.min_runtime}
              onChange={(e) => update("min_runtime", Number(e.target.value))}
              className={numberInputClass}
            />
          </label>

          <div className="flex items-center justify-between text-xs text-zinc-300">
            Wildcards allowance
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                disabled={value.wildcards_budget === -1}
                value={value.wildcards_budget === -1 ? "" : value.wildcards_budget}
                onChange={(e) => update("wildcards_budget", Number(e.target.value))}
                className={cn(numberInputClass, "disabled:opacity-40")}
              />
              <label className="flex items-center gap-1 text-[11px] text-zinc-500">
                <input
                  type="checkbox"
                  checked={value.wildcards_budget === -1}
                  onChange={(e) => update("wildcards_budget", e.target.checked ? -1 : 2)}
                  className="accent-accent"
                />
                Unlimited
              </label>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const numberInputClass =
  "w-16 rounded border border-app-border bg-app-bg px-2 py-1 text-right text-zinc-100 focus:border-accent focus:outline-none";

function PresetPill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "border-accent bg-accent/10 text-accent"
          : "border-app-border text-zinc-500 hover:text-zinc-200",
      )}
    >
      {children}
    </button>
  );
}
