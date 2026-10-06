import type { ReactNode } from "react";
import type { DiscoveryCandidate, FilterSpec } from "../../types/api";
import { candidateCountries } from "./ModeFilterBar";

export default function DirectorsPicks({
  candidates, matchOrder, visited, specs, allowRepeats, renderCard, onPick, slot,
}: {
  candidates: DiscoveryCandidate[];
  matchOrder: number[];
  visited: Set<string>;
  specs: FilterSpec[];
  allowRepeats: boolean;
  renderCard: (candidate: DiscoveryCandidate) => ReactNode;
  onPick: (candidate: DiscoveryCandidate) => void;
  slot?: ReactNode;
}) {
  const eligible = candidates.filter((candidate) => allowRepeats || !candidate.already_in_run);
  if (!eligible.length) return null;
  const ranks = new Map(matchOrder.map((id, index) => [id, index]));
  const bestMatch = [...eligible].sort((a, b) => (ranks.get(a.movie_id) ?? Infinity) - (ranks.get(b.movie_id) ?? Infinity))[0];
  const modeScore = (candidate: DiscoveryCandidate): number => {
    if (candidate.tug_points != null) return candidate.tug_points;
    if (candidate.semantic_score != null) return candidate.semantic_score;
    if (candidate.tier_compliant != null) return candidate.tier_compliant ? 1 : -1;
    if (candidate.narrative_delta != null) return Math.abs(candidate.narrative_delta);
    if (candidate.year_delta != null) return Math.abs(candidate.year_delta);
    if (specs.some((spec) => spec.source === "new_country")) {
      return candidateCountries(candidate).filter((code) => !visited.has(code)).length;
    }
    return candidate.connections.length;
  };
  const remaining = eligible.filter((candidate) => candidate.movie_id !== bestMatch.movie_id);
  const bestMode = [...remaining].sort((a, b) => modeScore(b) - modeScore(a))[0];
  const underdog = [...remaining].filter((candidate) => candidate.movie_id !== bestMode?.movie_id &&
    !candidate.constraint_unverified && candidate.tier_compliant !== false && (candidate.popularity ?? 0) >= 1)
    .sort((a, b) => (a.popularity ?? Infinity) - (b.popularity ?? Infinity))[0];
  const picks = [
    { label: "Best match", candidate: bestMatch },
    { label: "Best for mode", candidate: bestMode },
    { label: "Underdog", candidate: underdog },
  ];
  return (
    <section aria-label="Director's Picks" className="rounded-xl border border-accent/30 bg-accent/5 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-zinc-100">Director's Picks</h3>
        <button type="button" onClick={() => onPick(eligible[Math.floor(Math.random() * eligible.length)])}
          className="rounded-md bg-accent px-3 py-2 text-xs font-semibold text-zinc-950">
          🎲 Pick for me
        </button>
      </div>
      {slot ?? <div className="grid gap-3 sm:grid-cols-3">
        {picks.map(({ label, candidate }) => candidate && <div key={label} className="flex min-w-0 flex-col gap-1">
          <p className="text-xs font-semibold text-accent">{label}</p>
          {renderCard(candidate)}
        </div>)}
      </div>}
    </section>
  );
}
