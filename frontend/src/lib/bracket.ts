import type { Bracket, BracketMatchup, BracketRound } from "../types/api";

export const MARCH_MADNESS = "march_madness";
export const BRACKET_SIZE = 16;

export const BRACKET_ROUNDS: { key: BracketRound; label: string }[] = [
  { key: "round_of_16", label: "Round of 16" },
  { key: "quarterfinals", label: "Quarterfinals" },
  { key: "semifinals", label: "Semifinals" },
  { key: "finals", label: "Finals" },
];

/** Both films are known and no winner yet: the matchup can be played now. */
export function isActiveMatchup(matchup: BracketMatchup): boolean {
  return matchup.a !== null && matchup.b !== null && matchup.winner === null;
}

export function activeMatchupCount(bracket: Bracket): number {
  return BRACKET_ROUNDS.reduce(
    (total, round) => total + bracket[round.key].filter(isActiveMatchup).length,
    0,
  );
}
