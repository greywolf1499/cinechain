import { cn } from "../lib/cn";
import { DEFAULT_GENRE_CYCLE, pendulumState } from "../lib/pendulum";
import type { RulesConfig } from "../types/api";

/** The Genre Pendulum's header meter: the genre the next film must carry, how far into the swing the
 * run is, what swings in next, and the whole cycle with the current genre lit. */
export default function PendulumMeter({
  rules,
  stepsLogged,
  finished,
}: {
  rules: RulesConfig;
  stepsLogged: number;
  finished: boolean;
}) {
  const state = pendulumState(rules, stepsLogged);
  const cycle = rules.genre_cycle?.length ? rules.genre_cycle : DEFAULT_GENRE_CYCLE;
  const currentIndex = Math.floor(stepsLogged / state.frequency) % cycle.length;

  return (
    <section
      aria-label="Genre Pendulum"
      className="mb-5 rounded-xl border border-red-400/30 bg-red-500/5 px-4 py-3"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <p className="text-sm font-semibold text-red-200">
          <span aria-hidden>🎯 </span>
          Current Swing: {state.target}{" "}
          <span className="font-normal text-red-300/80">
            (Step {state.position} of {state.frequency})
          </span>
        </p>
        <p className="text-sm text-zinc-400">
          <span aria-hidden>→ </span>Next: <span className="font-medium text-zinc-200">{state.nextTarget}</span>
        </p>
        <div className="ml-auto flex items-center gap-1" aria-hidden>
          {Array.from({ length: state.frequency }, (_, i) => (
            <span
              key={i}
              className={cn(
                "h-2 w-5 rounded-full",
                i < state.position - 1 ? "bg-red-400/60" : i === state.position - 1 ? "bg-red-300" : "bg-app-surface-hover",
              )}
            />
          ))}
        </div>
      </div>
      <ol className="mt-2 flex flex-wrap items-center gap-1.5" aria-label="Genre cycle">
        {cycle.map((genre, index) => (
          <li
            key={`${genre}-${index}`}
            aria-current={index === currentIndex ? "step" : undefined}
            className={cn(
              "rounded-full px-2.5 py-0.5 text-[11px] font-medium",
              index === currentIndex && !finished
                ? "bg-red-400 text-zinc-950 shadow-[0_0_10px_rgba(248,113,113,0.5)]"
                : "bg-app-surface-hover text-zinc-400",
            )}
          >
            {genre}
          </li>
        ))}
      </ol>
      <p className="mt-1.5 text-[11px] text-zinc-500">
        The next film must carry {state.target} and share a genre with the last one.
      </p>
    </section>
  );
}
