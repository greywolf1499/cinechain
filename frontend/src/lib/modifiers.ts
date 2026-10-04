import { STANDALONE_MODES, TRACKER_MODES, usesCastLinks } from "./gameModes";
import type { RulesConfig } from "../types/api";

/** Pair rules (Engine V3) that can be layered on any graph engine. `require_cast_link` is the
 * fourth modifier but is already part of the standalone modes' own options. */
export const MODIFIER_KEYS = [
  "require_cast_link",
  "chrono_direction",
  "runtime_staircase",
  "country_cooldown",
] as const;

export const MAX_COUNTRY_COOLDOWN = 10;
/** Modes whose own rule already is one of the modifiers, and the value it defaults to. */
export const DEFAULT_COOLDOWN: Record<string, number> = { world_passport: 3 };

export function supportsModifiers(gameType: string, capabilities?: string[]): boolean {
  return capabilities ? capabilities.includes("modifiers") : !TRACKER_MODES.has(gameType);
}

/** Which modifier controls a mode offers (Chrono's direction is its own mode option). */
export function availableModifiers(gameType: string): {
  castLink: boolean;
  chrono: boolean;
  staircase: boolean;
  cooldown: boolean;
} {
  return {
    castLink: STANDALONE_MODES.has(gameType),
    // Chrono is Chrono Climb's own rule; Historical Time-Travel has its own (setting-year) direction.
    chrono: gameType !== "chrono_climb" && gameType !== "historical_time_travel",
    staircase: true,
    cooldown: true,
  };
}

export function effectiveCooldown(gameType: string, rules: RulesConfig): number {
  return rules.country_cooldown ?? DEFAULT_COOLDOWN[gameType] ?? 0;
}

/** How many optional modifiers are switched on beyond the mode's own rule. */
export function activeModifierCount(gameType: string, rules: RulesConfig): number {
  const have = availableModifiers(gameType);
  let count = 0;
  if (have.castLink && rules.require_cast_link) count++;
  if (have.chrono && rules.chrono_direction) count++;
  if (rules.runtime_staircase) count++;
  if (effectiveCooldown(gameType, rules) !== (DEFAULT_COOLDOWN[gameType] ?? 0)) count++;
  return count;
}

/** The rules with every modifier cleared (switching mode must not leak the old mode's tweaks). */
export function clearModifiers(rules: RulesConfig): RulesConfig {
  const next: RulesConfig = { ...rules };
  delete next.require_cast_link;
  delete next.chrono_direction;
  delete next.runtime_staircase;
  delete next.country_cooldown;
  delete next.direction;
  return next;
}

/** The modifier keys to send when creating a run: only what's on, only where supported. */
export function modifierPayload(
  gameType: string,
  rules: RulesConfig,
  capabilities?: string[],
): Partial<RulesConfig> {
  if (!supportsModifiers(gameType, capabilities)) return {};
  const have = availableModifiers(gameType);
  const payload: Partial<RulesConfig> = {};
  if (have.castLink) payload.require_cast_link = !!rules.require_cast_link;
  if (gameType === "historical_time_travel") {
    payload.direction = rules.direction ?? "climb";
  } else if (gameType === "chrono_climb") {
    payload.chrono_direction = rules.chrono_direction ?? rules.direction ?? "climb";
  } else if (rules.chrono_direction) {
    payload.chrono_direction = rules.chrono_direction;
  }
  if (rules.runtime_staircase) payload.runtime_staircase = rules.runtime_staircase;
  if (rules.country_cooldown != null) payload.country_cooldown = rules.country_cooldown;
  return payload;
}

export interface ModifierWarning {
  /** "danger" = the run will almost certainly dead-end; "caution" = a thin pool. */
  level: "danger" | "caution";
  /** The combination, in a sentence. */
  headline: string;
  /** Why it is hard: what each rule removes from the pool. */
  why: string;
}

/** Anti-synergistic stacks: rules that each narrow the pool so much together that a run
 * would stall. Purely advisory - the backend still accepts them. */
export function modifierWarnings(gameType: string, rules: RulesConfig): ModifierWarning[] {
  const warnings: ModifierWarning[] = [];
  const castLinked = usesCastLinks(gameType, rules);
  const chrono = gameType === "chrono_climb" || !!rules.chrono_direction;
  const staircase = !!rules.runtime_staircase;
  const cooldown = effectiveCooldown(gameType, rules);
  const orderingRules = Number(chrono) + Number(staircase);
  const chronoName = gameType === "chrono_climb" ? "Chrono Climb" : "Chrono";

  if (chrono && staircase) {
    warnings.push({
      level: "caution",
      headline: `${chronoName} + Runtime Staircase creates a double constraint that may exhaust small local libraries.`,
      why: "Every pick must be both newer and longer (or shorter) than the last. Each step discards roughly half of the remaining films, and the ratchet only tightens - chains stall quickly unless your cache holds thousands of films.",
    });
  }
  if (gameType === "canon_island" && orderingRules >= 1) {
    warnings.push({
      level: orderingRules >= 2 ? "danger" : "caution",
      headline: "Canon Island + an ordering rule leaves a very thin pool.",
      why: "The island is one curated list, and every hop must also share an actor with the last film. Adding a year or runtime ordering on top leaves only a handful of legal next films - often none.",
    });
  }
  if (gameType === "semantic_trope" && castLinked) {
    warnings.push({
      level: orderingRules >= 1 ? "danger" : "caution",
      headline: "Strict plot similarity + a shared-cast link rarely agree.",
      why: "Films with near-identical plots are seldom made by the same actors, so few candidates satisfy both rules at once.",
    });
  }
  if (gameType === "aesthetic_gradient" && castLinked && orderingRules >= 1) {
    warnings.push({
      level: "caution",
      headline: "Poster colours + shared cast + an ordering rule leave very few candidates.",
      why: "Each rule is independent of the others (colour has nothing to do with cast or year), so the overlap of all three is tiny.",
    });
  }
  if (gameType === "auteur_relay" && orderingRules >= 2) {
    warnings.push({
      level: "danger",
      headline: "Auteur Relay + Chrono + Runtime Staircase is close to impossible.",
      why: "Links must alternate actor, director, actor... and every film must also climb in both year and runtime. Directors have small filmographies, so the chain runs out almost immediately.",
    });
  }
  if (cooldown >= 6 && castLinked) {
    warnings.push({
      level: "caution",
      headline: `A ${cooldown}-step country cooldown on a shared-cast chain locks out most of the casting pool.`,
      why: "Actors mostly work in one country. Locking out the last several countries removes most of the filmographies you could hop through, especially with a US-heavy library.",
    });
  }
  return warnings;
}
