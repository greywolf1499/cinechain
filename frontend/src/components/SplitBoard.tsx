import { useState } from "react";
import { Loader2 } from "lucide-react";
import HouseholdRatingModal from "./HouseholdRatingModal";
import MoviePoster from "./MoviePoster";
import PlayerAvatar from "./PlayerAvatar";
import SplitMeter from "./SplitMeter";
import { cn } from "../lib/cn";
import { useSplitPool } from "../lib/queries";
import {
  SPLIT_TEAMS,
  settlementOf,
  targetPoints,
  type SplitTeam,
} from "../lib/splitScore";
import type { RunDetail, SplitCandidate, UserSummary } from "../types/api";

const SCAN_BATCH = 10;

/** The head-to-head scoreboard, the rated history and the pool of films worth splitting on. */
export default function SplitBoard({
  run,
  users,
}: {
  run: RunDetail;
  users: UserSummary[] | undefined;
}) {
  const locked = run.status !== "active";
  const [scan, setScan] = useState(0);
  const [film, setFilm] = useState<SplitCandidate | null>(null);
  const pool = useSplitPool(run.id, scan);
  const scores = run.rules_config.split_scores ?? { team_a: 0, team_b: 0 };
  const target = targetPoints(run.rules_config);
  const players = run.rules_config.split_players ?? { team_a: null, team_b: null };
  const rated = run.steps.filter((step) => settlementOf(step) !== null);

  return (
    <section aria-label="Rotten Tomatoes Split" className="flex flex-col gap-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto_1fr] sm:items-stretch">
        <TeamCard team="team_a" points={scores.team_a} target={target} userId={players.team_a} users={users} />
        <div className="flex items-center justify-center text-xs font-bold uppercase tracking-widest text-zinc-600">
          first to {target}
        </div>
        <TeamCard team="team_b" points={scores.team_b} target={target} userId={players.team_b} users={users} />
      </div>

      {rated.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Settled films</h3>
          <ul className="flex flex-col gap-1.5">
            {rated.map((step) => {
              const settled = settlementOf(step)!;
              return (
                <li
                  key={step.id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-app-border bg-app-bg/60 px-3 py-2 text-xs"
                >
                  <span className="min-w-0 flex-1 truncate font-medium text-zinc-100">{step.movie_title}</span>
                  <span className="text-zinc-500">
                    🍅 {settled.critic_score}% · 🍿 {settled.audience_score}% · household {settled.household_score}
                  </span>
                  <span className="font-semibold text-emerald-300">+1 {SPLIT_TEAMS[settled.point_to].emoji}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {!locked && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Films with a split verdict</h3>
            {pool.data?.omdb_enabled && (
              <button
                type="button"
                disabled={pool.isFetching}
                onClick={() => setScan((n) => n + SCAN_BATCH)}
                className="flex items-center gap-1.5 rounded-md border border-app-border px-2.5 py-1.5 text-[11px] font-medium text-zinc-300 hover:bg-app-surface-hover disabled:opacity-50"
              >
                {pool.isFetching && <Loader2 className="h-3 w-3 animate-spin" />}
                Rate {SCAN_BATCH} more cached films
              </button>
            )}
          </div>
          {pool.isLoading ? (
            <p className="text-xs text-zinc-500">Loading the pool...</p>
          ) : pool.data && pool.data.candidates.length > 0 ? (
            <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {pool.data.candidates.map((candidate) => (
                <li
                  key={candidate.movie_id}
                  className="flex items-center gap-3 rounded-xl border border-app-border bg-app-bg/60 p-2.5"
                >
                  <MoviePoster path={candidate.poster_path} title={candidate.title} className="w-12 shrink-0" />
                  <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                    <p className="truncate text-sm font-medium text-zinc-100">
                      {candidate.title}
                      {candidate.year && <span className="font-normal text-zinc-500"> ({candidate.year})</span>}
                    </p>
                    <SplitMeter
                      critic={candidate.critic_score}
                      audience={candidate.audience_score}
                      divergence={candidate.divergence}
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => setFilm(candidate)}
                    className="shrink-0 rounded-md bg-accent px-2.5 py-1.5 text-[11px] font-semibold text-zinc-950 hover:bg-accent-strong"
                  >
                    Log Movie
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-md border border-app-border bg-app-bg/60 px-3 py-3 text-xs text-zinc-400">
              {pool.data && !pool.data.omdb_enabled
                ? "No split films yet - Rotten Tomatoes and IMDb scores come from OMDb, which isn't configured. Add an OMDb key in Settings."
                : `No split films yet - scores are fetched as films are browsed. Rate a few more cached films to find ones the critics and the crowd disagree on by ${
                    pool.data?.min_divergence ?? 25
                  }+ points.`}
            </p>
          )}
        </div>
      )}

      <HouseholdRatingModal runId={run.id} film={film} onClose={() => setFilm(null)} />
    </section>
  );
}

function TeamCard({
  team,
  points,
  target,
  userId,
  users,
}: {
  team: SplitTeam;
  points: number;
  target: number;
  userId: string | null;
  users: UserSummary[] | undefined;
}) {
  const info = SPLIT_TEAMS[team];
  const user = userId ? users?.find((u) => u.id === userId) : undefined;
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-xl border px-4 py-3",
        team === "team_a" ? "border-red-400/40 bg-red-500/5" : "border-amber-400/40 bg-amber-500/5",
      )}
    >
      <span className="text-3xl" aria-hidden>
        {info.emoji}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-zinc-100">{info.label}</p>
        <p className="flex items-center gap-1.5 truncate text-xs text-zinc-500">
          {user ? (
            <>
              <PlayerAvatar name={user.display_name} size="sm" className="h-5 w-5 text-[9px]" /> {user.display_name}
            </>
          ) : (
            "Waiting for a partner"
          )}
        </p>
      </div>
      <p className="text-2xl font-bold tabular-nums text-zinc-100" aria-label={`${points} of ${target} points`}>
        {points}
        <span className="text-sm font-medium text-zinc-600"> / {target}</span>
      </p>
    </div>
  );
}
