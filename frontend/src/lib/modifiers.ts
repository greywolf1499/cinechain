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
    chrono: gameType !== "chrono_climb",
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
  if (gameType === "chrono_climb") {
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
  message: string;
}

/** Anti-synergistic stacks: rules that each narrow the pool so much together that a run
 * would stall. Purely advisory - the backend still accepts them. */
export function modifierWarnings(gameType: string, rules: RulesConfig): ModifierWarning[] {
  const warnings: ModifierWarning[] = [];
  const castLinked = usesCastLinks(gameType, rules);
  const chrono = gameType === "chrono_climb" || !!rules.chrono_direction;
  const staircase = !!rules.runtime_staircase;
  const cooldown = effectiveCooldown(gameType, rules);
  const strictOrderRules = Number(chrono) + Number(staircase);

  if (gameType === "canon_island" && strictOrderRules >= 1) {
    warnings.push({
      level: strictOrderRules >= 2 ? "danger" : "caution",
      message:
        strictOrderRules >= 2
          ? "Canon Island + shared cast + Chrono + Runtime Staircase leaves almost no valid films - the run will dead-end fast."
          : "Canon Island already limits you to one curated list with a shared actor; an ordering rule on top makes the pool very thin.",
    });
  }
  if (gameType === "semantic_trope" && castLinked) {
    warnings.push({
      level: strictOrderRules >= 1 ? "danger" : "caution",
      message:
        "Strict plot similarity plus a shared-cast link rarely agree - few films match both, so expect a tiny pool.",
    });
  }
  if (gameType === "aesthetic_gradient" && castLinked && strictOrderRules >= 1) {
    warnings.push({
      level: "caution",
      message: "Poster colours, shared cast and an ordering rule together leave very few candidates.",
    });
  }
  if (gameType === "auteur_relay" && strictOrderRules >= 2) {
    warnings.push({
      level: "danger",
      message: "Alternating actor/director links plus Chrono and Runtime Staircase is close to impossible.",
    });
  }
  if (chrono && staircase && gameType !== "canon_island" && gameType !== "semantic_trope") {
    warnings.push({
      level: "caution",
      message: "Chrono and Runtime Staircase both force a one-way ratchet - chains get short quickly.",
    });
  }
  if (cooldown >= 6 && castLinked) {
    warnings.push({
      level: "caution",
      message: `A ${cooldown}-step country cooldown on a shared-cast chain locks out most of the casting pool.`,
    });
  }
  return warnings;
}
