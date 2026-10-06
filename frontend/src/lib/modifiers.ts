import { usesCastLinks } from "./gameModes";
import type { EngineMeta, ModifierMeta, ModifierParamValue, RulesConfig } from "../types/api";

export function supportsModifiers(engine?: EngineMeta): boolean {
  return !!engine?.modifiers.some((spec) => spec.compatible);
}

export function modifierParams(spec: ModifierMeta, rules: RulesConfig): Record<string, ModifierParamValue> | null {
  const entry = rules.modifiers?.find((item) => item.key === spec.key);
  if (entry) return entry.params;
  // Legacy aliases remain readable; all new selections use registry entries.
  if (spec.key === "chrono_direction" && (rules.chrono_direction ?? rules.direction)) {
    return { direction: rules.chrono_direction ?? rules.direction ?? "climb" };
  }
  if (spec.key === "runtime_staircase" && rules.runtime_staircase) return { direction: rules.runtime_staircase };
  if (spec.key === "country_cooldown" && rules.country_cooldown != null) {
    return rules.country_cooldown > 0 ? { steps: rules.country_cooldown } : { steps: 0 };
  }
  if (spec.key === "require_cast_link" && rules.require_cast_link != null) return { enabled: rules.require_cast_link };
  return spec.default_params;
}

export function defaultModifierParams(spec: ModifierMeta): Record<string, ModifierParamValue> {
  return Object.fromEntries(Object.entries(spec.params_schema.properties)
    .filter(([, schema]) => schema.default !== undefined)
    .map(([key, schema]) => [key, schema.default!]));
}

export function setModifier(
  rules: RulesConfig, spec: ModifierMeta, params: Record<string, ModifierParamValue> | null,
): RulesConfig {
  const modifiers = (rules.modifiers ?? []).filter((entry) => entry.key !== spec.key);
  // Empty params would re-enable the schema default, so explicit false/zero overrides mode defaults.
  if (params) modifiers.push({ key: spec.key, params });
  else if (spec.default_params) {
    const inactive: Record<string, ModifierParamValue> = spec.key === "country_cooldown"
      ? { steps: 0 } : spec.key === "chrono_direction" ? spec.default_params : { enabled: false };
    modifiers.push({ key: spec.key, params: inactive });
  }
  const next = { ...rules, modifiers };
  if (spec.key === "chrono_direction") { delete next.chrono_direction; delete next.direction; }
  if (spec.key === "runtime_staircase") delete next.runtime_staircase;
  if (spec.key === "country_cooldown") delete next.country_cooldown;
  if (spec.key === "require_cast_link") delete next.require_cast_link;
  return next;
}

export function modifierEnabled(params: Record<string, ModifierParamValue> | null): boolean {
  return !!params && params.enabled !== false && params.steps !== 0;
}

export function effectiveCooldown(_gameType: string, rules: RulesConfig, engine?: EngineMeta): number {
  const nested = rules.modifiers?.find((entry) => entry.key === "country_cooldown")?.params.steps;
  if (typeof nested === "number") return nested;
  const fallback = engine?.modifiers.find((spec) => spec.key === "country_cooldown")?.default_params?.steps;
  return rules.country_cooldown ?? (typeof fallback === "number" ? fallback : 0);
}

export function activeModifierCount(engine: EngineMeta, rules: RulesConfig): number {
  return engine.modifiers.filter((spec) => spec.compatible && modifierEnabled(modifierParams(spec, rules))
    && !spec.default_params).length;
}

/** The rules with every modifier cleared (switching mode must not leak the old mode's tweaks). */
export function clearModifiers(rules: RulesConfig): RulesConfig {
  const next: RulesConfig = { ...rules };
  delete next.require_cast_link;
  delete next.chrono_direction;
  delete next.runtime_staircase;
  delete next.country_cooldown;
  delete next.direction;
  delete next.modifiers;
  return next;
}

/** The modifier keys to send when creating a run: only what's on, only where supported. */
export function modifierPayload(
  engine: EngineMeta | undefined,
  rules: RulesConfig,
): Partial<RulesConfig> {
  if (!supportsModifiers(engine) || !engine) return {};
  const payload: Partial<RulesConfig> = {};
  if (engine.game_type === "historical_time_travel") {
    payload.direction = rules.direction ?? "climb";
  }
  payload.modifiers = engine.modifiers.filter((spec) => spec.compatible)
    .flatMap((spec) => {
      const params = modifierParams(spec, rules);
      return params ? [{ key: spec.key, params }] : [];
    });
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
  const chrono = gameType === "chrono_climb" || !!rules.chrono_direction || !!rules.modifiers?.some((m) => m.key === "chrono_direction");
  const staircase = !!rules.runtime_staircase || !!rules.modifiers?.some((m) => m.key === "runtime_staircase");
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
