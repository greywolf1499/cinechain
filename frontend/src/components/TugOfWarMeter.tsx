import { cn } from "../lib/cn";
import { TUG_DIMENSIONS, tugEffectLabel, tugMomentum, tugTarget } from "../lib/tugOfWar";
import { GlossaryChip } from "./HowToPlay";
import type { RulesConfig, RunParticipant, UserSummary } from "../types/api";
import PlayerAvatar from "./PlayerAvatar";

/** The rope: Team B at the left end (-target), Team A at the right (+target). The knot sits at the
 * current momentum (team A points minus team B points) and the run is won when it reaches an end. */
export default function TugOfWarMeter({
  rules,
  participants,
  users,
  finished,
}: {
  rules: RulesConfig;
  participants: RunParticipant[];
  users: UserSummary[] | undefined;
  finished: boolean;
}) {
  const target = tugTarget(rules);
  const dimension = TUG_DIMENSIONS[rules.dimension ?? "era"];
  const scores = rules.tug_scores ?? { team_a: 0, team_b: 0 };
  const momentum = tugMomentum(rules);
  const clamped = Math.max(-target, Math.min(target, momentum));
  const position = ((clamped + target) / (2 * target)) * 100;

  const ordered = [...participants].sort(
    (a, b) => Number(b.role === "owner") - Number(a.role === "owner") || a.joined_at.localeCompare(b.joined_at),
  );
  const nameOf = (userId: string | null | undefined, fallback: string) =>
    (userId && users?.find((u) => u.id === userId)?.display_name) || fallback;
  const players = rules.tug_players ?? {
    team_a: ordered[0]?.user_id ?? null,
    team_b: ordered[1]?.user_id ?? null,
  };
  const nameA = nameOf(players.team_a, "Team A");
  const nameB = nameOf(players.team_b, "Team B");
  const tugState = rules.tug_momentum;
  const v3 = rules.tug_rules_version === 3;
  const nextTeam = tugState?.next_team ?? "team_a";
  const nextName = nextTeam === "team_a" ? nameA : nameB;
  const nextLabel =
    nextTeam === "team_a" ? dimension.teamA(rules) : dimension.teamB(rules);
  const ticks = Array.from({ length: 2 * target + 1 }, (_, i) => i - target);
  const leader = momentum === 0 ? null : momentum > 0 ? "a" : "b";

  return (
    <section
      aria-label="Tug of War"
      className="mb-5 rounded-xl border border-lime-400/30 bg-lime-500/5 px-4 py-3"
    >
      <div className="flex items-center gap-3">
        <TeamEnd
          name={nameB}
          label={dimension.teamB(rules)}
          score={scores.team_b}
          tone="b"
          leading={leader === "b"}
        />

        <div className="min-w-0 flex-1">
          <div
            role="meter"
            aria-label="Tug of War momentum"
            aria-valuemin={-target}
            aria-valuemax={target}
            aria-valuenow={clamped}
            aria-valuetext={
              momentum === 0
                ? "Level"
                : `${momentum > 0 ? nameA : nameB} leads by ${Math.abs(momentum)}`
            }
            className="relative h-9"
          >
            <div className="absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-full bg-app-surface-hover" />
            {/* fill from the centre knot-mark towards the leader */}
            <div
              className={cn(
                "absolute top-1/2 h-2 -translate-y-1/2 rounded-full transition-all duration-500",
                momentum > 0 ? "bg-sky-400/70" : "bg-orange-400/70",
              )}
              style={{
                left: `${Math.min(50, position)}%`,
                width: `${Math.abs(position - 50)}%`,
              }}
            />
            {ticks.map((tick) => (
              <span
                key={tick}
                className={cn(
                  "absolute top-1/2 w-px -translate-y-1/2 bg-zinc-600",
                  tick === 0 ? "h-6 bg-zinc-300" : "h-3",
                )}
                style={{ left: `${((tick + target) / (2 * target)) * 100}%` }}
              />
            ))}
            <span
              className={cn(
                "absolute top-1/2 flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-2 text-[10px] shadow-lg transition-all duration-500",
                finished ? "border-emerald-300 bg-emerald-500" : "border-lime-200 bg-lime-400",
              )}
              style={{ left: `${position}%` }}
              aria-hidden
            >
              🪢
            </span>
          </div>
          <div className="flex justify-between text-[10px] tabular-nums text-zinc-500">
            <span>-{target}</span>
            <span className="font-semibold text-zinc-300">
              Momentum {momentum > 0 ? `+${momentum}` : momentum}
            </span>
            <span>+{target}</span>
          </div>
        </div>

        <TeamEnd
          name={nameA}
          label={dimension.teamA(rules)}
          score={scores.team_a}
          tone="a"
          leading={leader === "a"}
        />
      </div>
      {!finished && (
        <p className="mt-2 text-center text-xs font-semibold text-lime-200">
          🪢 {nextName}&apos;s pull ({nextTeam === "team_a" ? "Team A" : "Team B"} · {nextLabel})
        </p>
      )}
      <div className="mt-1.5 flex flex-wrap justify-center gap-x-3 gap-y-1 text-[11px] text-zinc-500">
        <span>First to lead by {target} wins.</span>
        {v3 && <>
          <span>Round {(tugState?.rounds ?? 0) + (finished ? 0 : 1)} · victory after both pulls</span>
          {(["team_a", "team_b"] as const).map((team) => <GlossaryChip key={team} term="streak" className="text-orange-300">
            🔥 {team === "team_a" ? nameA : nameB} streak ×{tugState?.streaks?.[team] ?? 0}
          </GlossaryChip>)}
          {(["team_a", "team_b"] as const).filter((team) => tugState?.banks?.[team]).map((team) => <GlossaryChip key={team} term="bank" className="text-amber-300">
            ⚓ {team === "team_a" ? nameA : nameB} Bank (next pull ×2)
          </GlossaryChip>)}
        </>}
        {tugState?.streak_team && tugState.streak > 0 && (
          <GlossaryChip term="streak" className="text-orange-300">
            🔥 {tugState.streak_team === "team_a" ? nameA : nameB} streak ×{tugState.streak}
          </GlossaryChip>
        )}
        {tugState?.anchor && (
          <GlossaryChip term="bank" className="text-amber-300">
            ⚓ {tugState.anchor === "team_a" ? nameA : nameB} Bank (next pull ×2)
          </GlossaryChip>
        )}
        {tugState?.sudden_death && (
          <GlossaryChip term="sudden_death" className="font-semibold text-red-300">Sudden Death · target shrinks every {rules.sudden_death_every ?? 2} pulls{v3 ? " at round boundaries · Trailing team pulls first" : ""}</GlossaryChip>
        )}
      </div>
      {tugState?.pulls.length ? (
        <div aria-label="Recent Tug pulls" className="mt-2 flex flex-wrap justify-center gap-1">
          {tugState.pulls.slice(-8).map((pull, index) => (
            <GlossaryChip
              term={pull.kind === "invasion" ? "raid" : pull.kind === "neutral" ? "bank" : pull.kind === "home" ? "build" : "sudden_death"}
              key={pull.step_id}
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px]",
                pull.kind === "invasion"
                  ? "bg-orange-950/70 text-orange-200"
                  : pull.kind === "neutral" || pull.kind === "sudden_neutral"
                    ? "bg-amber-950/70 text-amber-200"
                    : "bg-lime-950/70 text-lime-200",
              )}
            >
              {tugEffectLabel(pull.kind, pull.points, v3 ? undefined : pull.multiplier)}
              {index === tugState.pulls.slice(-8).length - 1 ? " · now" : ""}
            </GlossaryChip>
          ))}
        </div>
      ) : null}
      <p className="mt-1.5 text-center text-[11px] text-zinc-500">
        {v3 ? "Build grows your streak; Raid breaks theirs. Each team can Bank its next pull ×2." : rules.dimension === "era"
          ? `Films from ${rules.era_a_before ?? 1975}–${rules.era_b_after ?? 2005} are neutral anchors; invasions steal ground.`
          : "Films without a country on record are neutral anchors; invasions steal ground."}
      </p>
    </section>
  );
}

function TeamEnd({
  name,
  label,
  score,
  tone,
  leading,
}: {
  name: string;
  label: string;
  score: number;
  tone: "a" | "b";
  leading: boolean;
}) {
  return (
    <div className="flex w-24 shrink-0 flex-col items-center gap-1 text-center">
      <PlayerAvatar
        name={name}
        className={cn(
          tone === "a" ? "border-sky-400 bg-sky-500/20 text-sky-200" : "border-orange-400 bg-orange-500/20 text-orange-200",
          leading && "ring-2 ring-lime-300/70",
        )}
      />
      <p className="w-full truncate text-xs font-medium text-zinc-200">{name}</p>
      <p className="w-full truncate text-[10px] text-zinc-500">{label}</p>
      <p className="text-lg font-semibold leading-none tabular-nums text-zinc-100">{score}</p>
    </div>
  );
}
