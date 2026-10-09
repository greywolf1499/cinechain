import type { FacetQuery } from "../types/api";

type FactValue = string | number | boolean | string[] | number[] | null | undefined;
export type RuleVerdict = true | false | null;

function kleeneNot(result: RuleVerdict): RuleVerdict {
	return result === null ? null : !result;
}

function kleeneAll(results: RuleVerdict[]): RuleVerdict {
	return results.includes(false) ? false : results.includes(null) ? null : true;
}

function kleeneAny(results: RuleVerdict[]): RuleVerdict {
	return results.includes(true) ? true : results.includes(null) ? null : false;
}

export function evaluateRule(query: FacetQuery, facts: Record<string, FactValue>): RuleVerdict {
	if (query.not) return kleeneNot(evaluateRule(query.not, facts));
	if (query.all) return kleeneAll(query.all.map((child) => evaluateRule(child, facts)));
	if (query.any) return kleeneAny(query.any.map((child) => evaluateRule(child, facts)));
	if (!query.facet || !query.op) return null;

	const actual = facts[query.facet];
	if (actual == null) return null;
	const expected = query.value;
	switch (query.op) {
		case "eq":
			return actual === expected;
		case "ne":
			return actual === expected ? false : true;
		case "lt":
		case "le":
		case "gt":
		case "ge":
			if (typeof actual !== "number" || typeof expected !== "number") return null;
			if (query.op === "lt") return actual < expected;
			if (query.op === "le") return actual <= expected;
			if (query.op === "gt") return actual > expected;
			return actual >= expected;
		case "contains":
			if (Array.isArray(actual)) return actual.includes(expected as never);
			if (typeof actual === "string" && typeof expected === "string") {
				return query.facet === "text"
					? new RegExp(`\\b${escapeRegExp(expected)}`, "i").test(actual)
					: actual.includes(expected);
			}
			return false;
		case "has_any":
			return Array.isArray(actual) && Array.isArray(expected)
				? expected.some((value) => actual.includes(value as never))
				: false;
		case "has_all":
			return Array.isArray(actual) && Array.isArray(expected)
				? expected.every((value) => actual.includes(value as never))
				: false;
	}
}

function escapeRegExp(value: string): string {
	return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
