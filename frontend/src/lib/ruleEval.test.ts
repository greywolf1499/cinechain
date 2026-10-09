import fixtureJson from "./ruleEval.fixture.json";
import { evaluateRule, type RuleVerdict } from "./ruleEval";
import type { FacetQuery } from "../types/api";

type FactValue = string | number | boolean | string[] | number[] | null;

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isQuery(value: unknown): value is FacetQuery {
	if (!isRecord(value)) return false;
	if (Array.isArray(value.all)) return value.all.every(isQuery);
	if (Array.isArray(value.any)) return value.any.every(isQuery);
	if (value.not !== undefined) return isQuery(value.not);
	return typeof value.facet === "string"
		&& ["eq", "ne", "lt", "le", "gt", "ge", "contains", "has_any", "has_all"].includes(String(value.op))
		&& (value.value === null || ["string", "number", "boolean"].includes(typeof value.value) || Array.isArray(value.value));
}

function isFixture(value: unknown): value is {
	name: string;
	query: FacetQuery;
	facts: Record<string, FactValue>;
	expected: RuleVerdict;
} {
	if (!isRecord(value) || typeof value.name !== "string" || !isQuery(value.query)) return false;
	if (value.expected !== true && value.expected !== false && value.expected !== null) return false;
	if (!isRecord(value.facts)) return false;
	return Object.values(value.facts).every((fact) =>
		fact === null
		|| typeof fact === "string"
		|| typeof fact === "number"
		|| typeof fact === "boolean"
		|| Array.isArray(fact) && fact.every((item) =>
			typeof item === "string" || typeof item === "number" || typeof item === "boolean",
		),
	);
}

for (const value of fixtureJson) {
	if (!isFixture(value)) throw new Error("Invalid rule evaluator parity fixture");
	const entry = value;
	const actual = evaluateRule(entry.query, entry.facts);
	if (actual !== entry.expected) {
		throw new Error(`${entry.name}: expected ${entry.expected}, got ${actual}`);
	}
}
