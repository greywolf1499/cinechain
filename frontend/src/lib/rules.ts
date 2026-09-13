import type { RulesConfig, RunStep } from "../types/api";

/** `allow_repeats !== "strict"` is this run's effective "allow movie repeats"
 * toggle - Standard/Purist default to "strict" (repeats disallowed), matching
 * the requested `allow_movie_repeats: bool = False` default for those presets. */
export function allowsMovieRepeats(rules: RulesConfig): boolean {
	return rules.allow_repeats !== "strict";
}

/** 1-based position of a movie in the run's step order, or null if not logged yet. */
export function findExistingStepNumber(
	steps: RunStep[],
	movieId: number,
): number | null {
	const index = steps.findIndex((step) => step.movie_id === movieId);
	return index === -1 ? null : index + 1;
}

export function wildcardsRemainingLabel(budget: number): string {
	return budget === -1 ? "unlimited" : String(budget);
}
