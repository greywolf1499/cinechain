import { Dices, GitBranch, Map as MapIcon, type LucideIcon } from "lucide-react";
import type { EngineMeta, Run } from "../types/api";
import { usesCastLinks } from "./gameModes";

export interface ToolDefinition {
	id: string;
	name: string;
	description: string;
	icon: LucideIcon;
	/** Route of the tool; null while the tool is only a placeholder. */
	to: string | null;
	/** Engine capability a run's game type must expose for the tool to work with it. */
	requiredCapability?: string;
}

/** Add future utilities here - the hub renders whatever is registered. */
export const TOOLS: ToolDefinition[] = [
	{
		id: "bridge",
		name: "Bridge Solver",
		description: "Find the shortest chain of shared actors between any two films.",
		icon: GitBranch,
		to: "/tools/bridge",
		requiredCapability: "solve_bridge",
	},
	{
		id: "map",
		name: "Map Generator",
		description: "Plot a run's films by country of origin on a world map.",
		icon: MapIcon,
		to: null,
	},
	{
		id: "bingo",
		name: "Bingo Generator",
		description: "Turn curated lists and genres into a printable watch-along bingo card.",
		icon: Dices,
		to: null,
	},
];

export type CompatibilityState = "compatible" | "incompatible" | "neutral";

export interface Compatibility {
	state: CompatibilityState;
	message: string | null;
}

/** Scaffolding for run-aware tools: a run's game type maps to an engine, and a
 * tool is usable only if that engine exposes the capability the tool needs.
 * With no active run a tool is simply available ("neutral"). */
export function toolCompatibility(
	tool: ToolDefinition,
	run: Pick<Run, "name" | "game_type" | "rules_config"> | null | undefined,
	engines: EngineMeta[] | undefined,
): Compatibility {
	if (!run || !tool.requiredCapability || !engines) return { state: "neutral", message: null };
	const engine = engines.find((e) => e.game_type === run.game_type);
	if (
		tool.requiredCapability === "solve_bridge" &&
		engine?.capabilities.includes("solve_bridge") &&
		!usesCastLinks(run.game_type, run.rules_config)
	) {
		return { state: "incompatible", message: "Disabled: this mode doesn't link films by cast" };
	}
	if (engine?.capabilities.includes(tool.requiredCapability)) {
		return { state: "compatible", message: `Compatible with current run (${run.name})` };
	}
	return { state: "incompatible", message: "Disabled: Incompatible with rigid tracker runs" };
}
