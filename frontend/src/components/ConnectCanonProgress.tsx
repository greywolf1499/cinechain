import type { RunDetail } from "../types/api";

export default function ConnectCanonProgress({
  run,
  score,
}: {
  run: RunDetail;
  score: number | null | undefined;
}) {
  const legs = run.rules_config.legs ?? [];
  const currentLeg = run.rules_config.current_leg ?? 0;
  if (!legs.length) return null;

  return (
    <section className="rounded-xl border border-app-border bg-app-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">Connect the Canon</h2>
          <p className="text-xs text-zinc-500">
            Start at the first waypoint and reach the next targets in order.
          </p>
        </div>
        {score != null && (
          <span className="rounded-full bg-accent/10 px-2.5 py-1 text-xs font-semibold text-accent">
            Score (hops - par): {score}
          </span>
        )}
      </div>
      <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {legs.map((leg, index) => {
          const reached = leg.reached_at_step != null;
          const active = index === currentLeg && !reached;
          const title = run.steps.find((step) => step.movie_id === leg.to)?.movie_title;
          return (
            <li
              key={`${leg.from}-${leg.to}`}
              className={`rounded-lg border px-3 py-2 ${
                reached
                  ? "border-emerald-600/50 bg-emerald-500/5"
                  : active
                    ? "border-accent/50 bg-accent/5"
                    : "border-app-border bg-app-bg"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-zinc-200">
                  Leg {index + 1}: {title ?? `Waypoint #${leg.to}`}
                </span>
                <span className="text-[10px] text-zinc-400">
                  {reached ? "Reached" : active ? "Current" : "Up next"}
                </span>
              </div>
              <p className="mt-1 text-[11px] text-zinc-500">
                {leg.par == null ? "Par unknown" : `Par ${leg.par} hops`}
              </p>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
