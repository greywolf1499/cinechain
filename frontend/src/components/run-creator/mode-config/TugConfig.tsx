import { TUG_DIMENSIONS, tugTargetDefault } from "../../../lib/tugOfWar";
import { useEngines } from "../../../lib/queries";
import type { TugDimension } from "../../../types/api";
import { cn } from "../../../lib/cn";
import type { ModeConfigProps } from "./types";

export default function TugConfig({ draft, update }: ModeConfigProps) {
  const { data: engines } = useEngines();
  const target = draft.rules.target_lead ?? tugTargetDefault(engines);
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-lime-400/30 bg-lime-500/5 p-3">
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-zinc-400">Dimension (what pulls the rope)</span>
        <div role="radiogroup" aria-label="Tug of War dimension" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {(Object.keys(TUG_DIMENSIONS) as TugDimension[]).map((key) => {
            const dimension = TUG_DIMENSIONS[key];
            const active = draft.tugDimension === key;
            return (
              <button
                key={key}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => update({ tugDimension: key })}
                className={cn(
                  "flex flex-col gap-0.5 rounded-lg border p-2.5 text-left transition-colors",
                  active ? "border-lime-400 bg-lime-500/10" : "border-app-border hover:border-zinc-600",
                )}
              >
                <span className="text-sm font-semibold text-zinc-100">{dimension.label}</span>
                <span className="text-[11px] text-lime-300">
                  Team A: {dimension.teamA(draft.rules)} &middot; Team B: {dimension.teamB(draft.rules)}
                </span>
                <span className="text-[11px] text-zinc-500">{dimension.detail}</span>
              </button>
            );
          })}
        </div>
      </div>
      <p className="text-[11px] text-zinc-500">
        You are Team A; the next participant you add is Team B. Home films build momentum, invasions
        steal ground, and neutral films set an anchor. First to lead by {target} wins.
      </p>
    </div>
  );
}
