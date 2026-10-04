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

/** What a forced (rule-breaking) step costs in this run: a Rabbit Hole life, else a wildcard. */
export interface ForcePricing {
	/** "life" | "wildcard" */
	noun: "life" | "wildcard";
	/** Plural, lower-case: "lives" | "wildcards" */
	plural: string;
	/** -1 = unlimited */
	remaining: number;
	exhausted: boolean;
	/** The confirm button's label. */
	confirmLabel: string;
}

export function forcePricing(rules: Pick<RulesConfig, "wildcards_budget" | "lives_remaining">): ForcePricing {
	if (typeof rules.lives_remaining === "number") {
		return {
			noun: "life",
			plural: "lives",
			remaining: rules.lives_remaining,
			exhausted: rules.lives_remaining <= 0,
			confirmLabel: "Spend a Life",
		};
	}
	const remaining = rules.wildcards_budget;
	return {
		noun: "wildcard",
		plural: "wildcards",
		remaining,
		exhausted: remaining !== -1 && remaining <= 0,
		confirmLabel: "Confirm Wildcard Jump",
	};
}

export function wildcardsRemainingLabel(budget: number): string {
	return budget === -1 ? "unlimited" : String(budget);
}
