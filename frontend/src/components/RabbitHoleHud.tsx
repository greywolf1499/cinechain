import { cn } from "../lib/cn";
import { rabbitHud } from "../lib/rabbitHole";
import type { RulesConfig } from "../types/api";

const TIER_STYLES = [
  "border-emerald-500/50 bg-emerald-500/10 text-emerald-300",
  "border-amber-500/50 bg-amber-500/10 text-amber-300",
  "border-sky-500/50 bg-sky-500/10 text-sky-300",
  "border-fuchsia-500/50 bg-fuchsia-500/10 text-fuchsia-300",
  "border-red-500/60 bg-red-500/10 text-red-300",
];

/** Hearts: lit while a life remains, a black heart once it is lost. */
export function Lives({ lives, maxLives, className }: { lives: number; maxLives: number; className?: string }) {
  return (
    <span
      role="img"
      aria-label={`${lives} of ${maxLives} lives remaining`}
      className={cn("inline-flex items-center gap-0.5 text-base leading-none", className)}
    >
      {Array.from({ length: maxLives }, (_, index) => {
        const alive = index < lives;
        return (
          <span
            key={index}
            title={alive ? "Life" : "Life lost"}
            className={cn("transition-all", alive ? "drop-shadow-[0_0_4px_rgba(244,63,94,0.7)]" : "opacity-60 grayscale")}
          >
            {alive ? "❤️" : "🖤"}
          </span>
        );
      })}
    </span>
  );
}

/** The Rabbit Hole's arcade status bar: depth, active tier, lives and the tier boundary ahead. */
export default function RabbitHoleHud({
  rules,
  depth,
  finished,
}: {
  rules: RulesConfig;
  depth: number;
  finished: boolean;
}) {
  const hud = rabbitHud(rules, depth);
  const style = TIER_STYLES[hud.tier.number - 1];
  return (
    <div className="mb-5 flex flex-col gap-2" aria-label="Rabbit Hole status">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-zinc-700 bg-zinc-950 px-5 py-3 font-mono shadow-[inset_0_0_24px_rgba(0,0,0,0.6)]">
        <p className="text-sm font-bold tracking-wide text-zinc-100">
          🕳️ Depth <span className="tabular-nums text-accent">{hud.depth}</span>
        </p>
        <span
          title={`Tier ${hud.tier.number}: ${hud.tier.name}`}
          className={cn("rounded-md border px-2.5 py-1 text-xs font-semibold", style)}
        >
          [ Tier {hud.tier.number}: {hud.tier.name}
          {hud.tier.number > 1 && ` (${hud.tier.rule})`} ]
        </span>
        <Lives lives={hud.lives} maxLives={hud.maxLives} className="ml-auto" />
      </div>
      {!finished && hud.warning && (
        <div
          role="alert"
          className="animate-pulse rounded-lg border border-amber-500/60 bg-amber-500/10 px-4 py-2 font-mono text-xs font-semibold text-amber-200"
        >
          {hud.warning}
        </div>
      )}
      {!finished && hud.lives === 0 && (
        <p className="rounded-lg border border-red-900/60 bg-red-950/30 px-4 py-2 font-mono text-xs font-semibold text-red-300">
          💀 No lives left - only a film that obeys the tier rule can continue the descent.
        </p>
      )}
    </div>
  );
}
