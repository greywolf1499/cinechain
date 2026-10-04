import { Skull } from "lucide-react";
import { rabbitSummary } from "../lib/rabbitHole";
import { Lives } from "./RabbitHoleHud";
import type { RulesConfig, RunStep } from "../types/api";

/** "Run Terminated": the Rabbit Hole's game over screen. */
export default function RabbitHoleGameOver({
  steps,
  rules,
  reason,
}: {
  steps: RunStep[];
  rules: RulesConfig;
  reason: string | null;
}) {
  const summary = rabbitSummary(steps);
  const stats = [
    { label: "Max Depth Reached", value: summary.maxDepth },
    { label: "Tiers Conquered", value: `${summary.tiersConquered} / 5` },
    { label: "Total Movies Watched", value: summary.moviesWatched },
  ];
  return (
    <section
      aria-label="Run terminated"
      className="mb-6 overflow-hidden rounded-2xl border border-red-700/70 bg-gradient-to-b from-red-950/60 via-zinc-950 to-zinc-950 px-6 py-8 text-center font-mono shadow-[0_0_40px_-10px_rgba(239,68,68,0.6)]"
    >
      <Skull className="mx-auto h-10 w-10 text-red-400" />
      <h2 className="mt-3 text-2xl font-black uppercase tracking-[0.3em] text-red-400">Run Terminated</h2>
      <p className="mt-2 text-sm text-zinc-400">
        {reason ?? "Succumbed to the Rabbit Hole"}
      </p>
      <Lives lives={0} maxLives={rules.max_lives ?? 3} className="mt-3 text-xl" />
      <dl className="mx-auto mt-6 grid max-w-xl grid-cols-1 gap-3 sm:grid-cols-3">
        {stats.map((stat) => (
          <div key={stat.label} className="rounded-xl border border-zinc-800 bg-zinc-900/70 px-3 py-3">
            <dd className="text-2xl font-bold tabular-nums text-zinc-100">{stat.value}</dd>
            <dt className="mt-1 text-[10px] uppercase tracking-wider text-zinc-500">{stat.label}</dt>
          </div>
        ))}
      </dl>
      <p className="mt-5 text-xs text-zinc-500">
        Deepest tier: {summary.tierReached.number} - {summary.tierReached.name}
      </p>
    </section>
  );
}
