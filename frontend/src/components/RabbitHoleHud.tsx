import { cn } from "../lib/cn";
import { ApiError } from "../lib/api";
import { rabbitHud, rabbitTiers } from "../lib/rabbitHole";
import { useRabbitHoleReroll, useRabbitHoleSkipCurse, useRunConstraint, useUpdateRun } from "../lib/queries";
import type { RulesConfig } from "../types/api";

const TIER_STYLES = [
  "border-emerald-500/50 bg-emerald-500/10 text-emerald-300",
  "border-amber-500/50 bg-amber-500/10 text-amber-300",
  "border-sky-500/50 bg-sky-500/10 text-sky-300",
  "border-fuchsia-500/50 bg-fuchsia-500/10 text-fuchsia-300",
  "border-red-500/60 bg-red-500/10 text-red-300",
];

export function RabbitInventory({ rules, depth }: { rules: RulesConfig; depth: number }) {
  if (rules.rh_rules_version !== 2 && rules.rh_rules_version !== 3) return null;
  const hud = rabbitHud(rules, depth);
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap gap-2">
        {rules.daily && <span className="rounded bg-sky-500/15 px-2 py-1 text-sky-200">📅 Daily Dive · shared UTC seed</span>}
        <span className="rounded bg-amber-500/10 px-2 py-1 text-amber-200">🎲 {rules.reroll_tokens ?? 0} free re-rolls</span>
        <span className="rounded bg-violet-500/10 px-2 py-1 text-violet-200">🛡️ {rules.relics?.skip_curse ?? 0} skip-curse relics</span>
        {rules.curse_skip === depth && <span className="text-violet-200">Newest curse skipped for this hop</span>}
        {hud.tier.curses?.map((curse, index) =>
          <span key={`${curse.predicate_id}:${index}`} className="rounded border border-red-500/30 px-2 py-1 text-red-200">☠️ Curse: {curse.rule}</span>)}
      </div>
      <details className="rounded-md border border-app-border p-2 text-zinc-400">
        <summary className="cursor-pointer">Procedural deck · {rabbitTiers(rules).length} tiers</summary>
        <ol className="mt-2 space-y-1">
          {rabbitTiers(rules).map((tier) =>
            <li key={tier.number}>Depth {tier.startDepth} · {tier.name}: {tier.rule}
              {tier.curses?.map((curse, index) => <span key={index} className="ml-2 text-red-300">+ {curse.rule}</span>)}
            </li>)}
        </ol>
      </details>
    </div>
  );
}

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
  runId,
  rules,
  depth,
  finished,
  onSearchManually,
}: {
  runId: string;
  rules: RulesConfig;
  depth: number;
  finished: boolean;
  onSearchManually: () => void;
}) {
  const hud = rabbitHud(rules, depth);
  const style = TIER_STYLES[Math.min(hud.tier.number - 1, TIER_STYLES.length - 1)];
  const { data: constraint } = useRunConstraint(runId);
  const reroll = useRabbitHoleReroll(runId);
  const skipCurse = useRabbitHoleSkipCurse(runId);
  const forfeit = useUpdateRun(runId);
  const deadEnd = constraint?.rabbit_hole?.dead_end === true;
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
          {hud.tierOverride && <span className="ml-1 text-fuchsia-200">Re-rolled</span>}
        </span>
        <Lives lives={hud.lives} maxLives={hud.maxLives} className="ml-auto" />
      </div>
      <RabbitInventory rules={rules} depth={depth} />
      {!finished && (rules.relics?.skip_curse ?? 0) > 0 && (hud.tier.curses?.length ?? 0) > 0 && rules.curse_skip !== depth && (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={skipCurse.isPending || reroll.isPending}
            onClick={() => skipCurse.mutate()}
            className="rounded-md border border-violet-500/40 px-3 py-1.5 text-xs text-violet-200 disabled:opacity-60">
            {skipCurse.isPending ? "Skipping…" : "🛡️ Skip newest curse (one hop)"}
          </button>
          {skipCurse.isError && <p role="alert" className="text-xs text-amber-300">
            {skipCurse.error instanceof ApiError ? skipCurse.error.message : "Could not skip this curse. Try again."}
          </p>}
        </div>
      )}
      {!finished && rules.allow_reroll !== false && hud.tier.number > 1 && (hud.lives >= 2 || (rules.reroll_tokens ?? 0) > 0) && !hud.tierOverride && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={reroll.isPending || skipCurse.isPending}
            onClick={() => reroll.mutate()}
            className="rounded-md border border-fuchsia-500/40 bg-fuchsia-500/10 px-3 py-1.5 font-mono text-xs font-semibold text-fuchsia-200 transition-colors hover:bg-fuchsia-500/20 disabled:opacity-60"
          >
            {reroll.isPending ? "Re-rolling…" : (rules.reroll_tokens ?? 0) > 0 ? "🎲 Re-roll tier (free relic)" : "🎲 Re-roll tier (−1 ❤️)"}
          </button>
          {reroll.isError && (
            <span role="alert" className="text-xs text-amber-300">
              {reroll.error instanceof ApiError ? reroll.error.message : "Could not re-roll this tier. Refresh and try again."}
            </span>
          )}
        </div>
      )}
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
      {!finished && deadEnd && (
        <section className="rounded-lg border border-red-700/60 bg-red-950/30 px-4 py-3 font-mono">
          <p role="alert" className="text-sm font-semibold text-red-200">
            🕳️ No way down: no valid cached links remain. A manual search may still find a film.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={forfeit.isPending}
              onClick={() => forfeit.mutate({ status: "forfeited" })}
              className="rounded-md bg-red-600 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-red-500 disabled:opacity-60"
            >
              Accept your fate
            </button>
            <button
              type="button"
              onClick={onSearchManually}
              className="rounded-md border border-app-border px-3 py-2 text-xs font-semibold text-zinc-200 transition-colors hover:bg-app-surface-hover"
            >
              Search manually
            </button>
          </div>
          {forfeit.isError && (
            <p role="alert" className="mt-2 text-xs text-amber-300">
              Could not forfeit the run. Refresh and try again.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
