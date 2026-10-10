import {
  Clapperboard,
  Combine,
  Compass,
  Dices,
  Drama,
  Globe2,
  History,
  Hourglass,
  Landmark,
  CalendarRange,
  Network,
  Swords,
  Palette,
  Popcorn,
  Skull,
  Trophy,
  VenetianMask,
  Sparkles,
  SwatchBook,
  type LucideIcon,
} from "lucide-react";
import type { RulesConfig } from "../types/api";

/** Visual identity for a game mode's card. Classes are spelled out in full so Tailwind keeps them. */
export interface GameModeStyle {
  icon: LucideIcon;
  category: ModeCategory;
  /** Tinted icon bubble + selected-card ring/glow. */
  bubble: string;
  ring: string;
  text: string;
}

export type ModeCategory =
  | "Cast Chains"
  | "Rule Chains"
  | "Versus/Co-op"
  | "Survival"
  | "Trackers & Tournaments";

export const GAME_MODE_STYLES: Record<string, GameModeStyle> = {
  cinechain: {
    icon: Network,
    category: "Cast Chains",
    bubble: "bg-amber-500/15 text-amber-300",
    ring: "border-amber-400 shadow-[0_0_0_1px_rgba(251,191,36,0.6),0_8px_30px_-8px_rgba(251,191,36,0.35)]",
    text: "text-amber-300",
  },
  auteur_relay: {
    icon: Clapperboard,
    category: "Cast Chains",
    bubble: "bg-orange-500/15 text-orange-300",
    ring: "border-orange-400 shadow-[0_0_0_1px_rgba(251,146,60,0.6),0_8px_30px_-8px_rgba(251,146,60,0.35)]",
    text: "text-orange-300",
  },
  meet_in_the_middle: {
    icon: Combine,
    category: "Versus/Co-op",
    bubble: "bg-cyan-500/15 text-cyan-300",
    ring: "border-cyan-400 shadow-[0_0_0_1px_rgba(34,211,238,0.6),0_8px_30px_-8px_rgba(34,211,238,0.35)]",
    text: "text-cyan-300",
  },
  grid_crawler: {
    icon: Dices,
    category: "Rule Chains",
    bubble: "bg-indigo-500/15 text-indigo-300",
    ring: "border-indigo-400 shadow-[0_0_0_1px_rgba(129,140,248,0.6),0_8px_30px_-8px_rgba(129,140,248,0.35)]",
    text: "text-indigo-300",
  },
  connect_canon: {
    icon: Landmark,
    category: "Cast Chains",
    bubble: "bg-emerald-500/15 text-emerald-300",
    ring: "border-emerald-400 shadow-[0_0_0_1px_rgba(52,211,153,0.6),0_8px_30px_-8px_rgba(52,211,153,0.35)]",
    text: "text-emerald-300",
  },
  canon_infiltration: {
    icon: Compass,
    category: "Cast Chains",
    bubble: "bg-orange-500/15 text-orange-300",
    ring: "border-orange-400 shadow-[0_0_0_1px_rgba(251,146,60,0.6),0_8px_30px_-8px_rgba(251,146,60,0.35)]",
    text: "text-orange-300",
  },
  genre_pendulum: {
    icon: Drama,
    category: "Rule Chains",
    bubble: "bg-red-500/15 text-red-300",
    ring: "border-red-400 shadow-[0_0_0_1px_rgba(248,113,113,0.6),0_8px_30px_-8px_rgba(248,113,113,0.35)]",
    text: "text-red-300",
  },
  crew_craft: {
    icon: SwatchBook,
    category: "Cast Chains",
    bubble: "bg-fuchsia-500/15 text-fuchsia-300",
    ring: "border-fuchsia-400 shadow-[0_0_0_1px_rgba(232,121,249,0.6),0_8px_30px_-8px_rgba(232,121,249,0.35)]",
    text: "text-fuchsia-300",
  },
  tug_of_war: {
    icon: Swords,
    category: "Versus/Co-op",
    bubble: "bg-lime-500/15 text-lime-300",
    ring: "border-lime-400 shadow-[0_0_0_1px_rgba(163,230,53,0.6),0_8px_30px_-8px_rgba(163,230,53,0.35)]",
    text: "text-lime-300",
  },
  canon_island: {
    icon: Landmark,
    category: "Cast Chains",
    bubble: "bg-emerald-500/15 text-emerald-300",
    ring: "border-emerald-400 shadow-[0_0_0_1px_rgba(52,211,153,0.6),0_8px_30px_-8px_rgba(52,211,153,0.35)]",
    text: "text-emerald-300",
  },
  decade_sieve: {
    icon: CalendarRange,
    category: "Trackers & Tournaments",
    bubble: "bg-sky-500/15 text-sky-300",
    ring: "border-sky-400 shadow-[0_0_0_1px_rgba(56,189,248,0.6),0_8px_30px_-8px_rgba(56,189,248,0.35)]",
    text: "text-sky-300",
  },
  roulette: {
    icon: Dices,
    category: "Trackers & Tournaments",
    bubble: "bg-pink-500/15 text-pink-300",
    ring: "border-pink-400 shadow-[0_0_0_1px_rgba(244,114,182,0.6),0_8px_30px_-8px_rgba(244,114,182,0.35)]",
    text: "text-pink-300",
  },
  chrono_climb: {
    icon: Hourglass,
    category: "Rule Chains",
    bubble: "bg-violet-500/15 text-violet-300",
    ring: "border-violet-400 shadow-[0_0_0_1px_rgba(167,139,250,0.6),0_8px_30px_-8px_rgba(167,139,250,0.35)]",
    text: "text-violet-300",
  },
  historical_time_travel: {
    icon: History,
    category: "Rule Chains",
    bubble: "bg-blue-500/15 text-blue-300",
    ring: "border-blue-400 shadow-[0_0_0_1px_rgba(96,165,250,0.6),0_8px_30px_-8px_rgba(96,165,250,0.35)]",
    text: "text-blue-300",
  },
  world_passport: {
    icon: Globe2,
    category: "Rule Chains",
    bubble: "bg-teal-500/15 text-teal-300",
    ring: "border-teal-400 shadow-[0_0_0_1px_rgba(45,212,191,0.6),0_8px_30px_-8px_rgba(45,212,191,0.35)]",
    text: "text-teal-300",
  },
  rabbit_hole: {
    icon: Skull,
    category: "Survival",
    bubble: "bg-red-500/15 text-red-300",
    ring: "border-red-400 shadow-[0_0_0_1px_rgba(248,113,113,0.6),0_8px_30px_-8px_rgba(248,113,113,0.35)]",
    text: "text-red-300",
  },
  march_madness: {
    icon: Trophy,
    category: "Trackers & Tournaments",
    bubble: "bg-yellow-500/15 text-yellow-300",
    ring: "border-yellow-400 shadow-[0_0_0_1px_rgba(250,204,21,0.6),0_8px_30px_-8px_rgba(250,204,21,0.35)]",
    text: "text-yellow-300",
  },
  method_actor: {
    icon: VenetianMask,
    category: "Trackers & Tournaments",
    bubble: "bg-fuchsia-500/15 text-fuchsia-300",
    ring: "border-fuchsia-400 shadow-[0_0_0_1px_rgba(232,121,249,0.6),0_8px_30px_-8px_rgba(232,121,249,0.35)]",
    text: "text-fuchsia-300",
  },
  auteur_marathon: {
    icon: Clapperboard,
    category: "Trackers & Tournaments",
    bubble: "bg-teal-500/15 text-teal-300",
    ring: "border-teal-400 shadow-[0_0_0_1px_rgba(45,212,191,0.6),0_8px_30px_-8px_rgba(45,212,191,0.35)]",
    text: "text-teal-300",
  },
  regional_deep_dive: {
    icon: Compass,
    category: "Trackers & Tournaments",
    bubble: "bg-lime-500/15 text-lime-300",
    ring: "border-lime-400 shadow-[0_0_0_1px_rgba(163,230,53,0.6),0_8px_30px_-8px_rgba(163,230,53,0.35)]",
    text: "text-lime-300",
  },
  rt_split: {
    icon: Popcorn,
    category: "Versus/Co-op",
    bubble: "bg-pink-500/15 text-pink-300",
    ring: "border-pink-400 shadow-[0_0_0_1px_rgba(244,114,182,0.6),0_8px_30px_-8px_rgba(244,114,182,0.35)]",
    text: "text-pink-300",
  },
  aesthetic_gradient: {
    icon: Palette,
    category: "Rule Chains",
    bubble: "bg-rose-500/15 text-rose-300",
    ring: "border-rose-400 shadow-[0_0_0_1px_rgba(251,113,133,0.6),0_8px_30px_-8px_rgba(251,113,133,0.35)]",
    text: "text-rose-300",
  },
  semantic_trope: {
    icon: Sparkles,
    category: "Rule Chains",
    bubble: "bg-indigo-500/15 text-indigo-300",
    ring: "border-indigo-400 shadow-[0_0_0_1px_rgba(129,140,248,0.6),0_8px_30px_-8px_rgba(129,140,248,0.35)]",
    text: "text-indigo-300",
  },
};

export const FALLBACK_MODE_STYLE: GameModeStyle = {
  icon: Compass,
  category: "Cast Chains",
  bubble: "bg-zinc-500/15 text-zinc-300",
  ring: "border-zinc-400 shadow-[0_0_0_1px_rgba(161,161,170,0.6)]",
  text: "text-zinc-300",
};

/** Compatibility copy for a cached/pre-S3 engine response, never the visual identity registry. */
export const LEGACY_MODE_COPY: Record<string, { tagline: string; tags: string[]; progression?: string[] }> = {
  cinechain: { tagline: "Six Degrees of Kevin Bacon", tags: ["Shared cast"] },
  auteur_relay: { tagline: "Actor, director, actor, director...", tags: ["Shared cast", "Shared director"] },
  meet_in_the_middle: { tagline: "Two partners, one tunnel", tags: ["Shared cast", "Co-op", "Two seeds"] },
  grid_crawler: { tagline: "Claim adjacent facet cells", tags: ["Board", "Adjacency", "Facet rules"] },
  connect_canon: { tagline: "Hit every canon waypoint", tags: ["Shared cast", "Waypoints", "Par"] },
  canon_infiltration: { tagline: "Reach the canon before hop limit", tags: ["Shared cast", "Target set", "Hop budget"] },
  genre_pendulum: { tagline: "The genre swings as you go", tags: ["Any film", "Genre cycle", "Genre overlap"] },
  crew_craft: { tagline: "Follow the craft, not just the cast", tags: ["Cast or crew", "Composer", "Cinematographer", "Writer", "Director"] },
  tug_of_war: { tagline: "Pull the rope your way", tags: ["Shared cast", "Two teams", "Era or geography"] },
  canon_island: { tagline: "Stay on the canon", tags: ["Shared cast", "One curated list"] },
  decade_sieve: { tagline: "One decade, no escape", tags: ["Any film", "One decade"] },
  roulette: { tagline: "Let the wheel decide", tags: ["Random pick"] },
  chrono_climb: { tagline: "Climb or descend through time", tags: ["Any film", "Release year"] },
  historical_time_travel: { tagline: "Travel through the eras stories are set in", tags: ["Any film", "Setting year", "Forward / Backward"] },
  world_passport: { tagline: "A new country every film", tags: ["Any film", "Country"] },
  rabbit_hole: {
    tagline: "Descend. Survive. Don't blink.", tags: ["Shared cast", "3 lives", "Rogue-like"],
    progression: ["Freefall", "Pre-2000", "Non-English", "Under 100 min", "B-movies"],
  },
  march_madness: {
    tagline: "16 films enter, one is crowned", tags: ["Tournament", "Watchlist", "Partner voting"],
    progression: ["Round of 16", "Quarters", "Semis", "Finals", "Champion"],
  },
  method_actor: {
    tagline: "One career, in order", tags: ["One actor", "Chronological", "Milestones"],
    progression: ["🐣 Debut", "🚀 Breakout", "🏆 Prestige Peak", "👑 Resurgence"],
  },
  auteur_marathon: {
    tagline: "One director, every feature", tags: ["One director", "Release order", "Filmography"],
    progression: ["🎬 First Feature", "🎞️ The Middle Years", "🏁 Final Film"],
  },
  regional_deep_dive: {
    tagline: "Conquer one corner of the canon", tags: ["Canon list", "Country", "Decade"],
    progression: ["🧭 Pick a Slice", "🗺️ Cross Off the Board", "🚩 Conquer It"],
  },
  rt_split: {
    tagline: "🍅 Critics vs 🍿 Audience", tags: ["Tomatometer split", "Head to head", "Household rating"],
    progression: ["🍅 Critic points", "🍿 Audience points", "🏆 First to 3"],
  },
  aesthetic_gradient: { tagline: "Fade poster to poster", tags: ["Any film", "Poster colour"] },
  semantic_trope: { tagline: "Follow the plot, not the cast", tags: ["Any film", "Plot similarity"] },
};

export const MODE_CATEGORIES: ModeCategory[] = [
  "Cast Chains",
  "Rule Chains",
  "Versus/Co-op",
  "Survival",
  "Trackers & Tournaments",
];

export function gameModeStyle(gameType: string): GameModeStyle {
  return GAME_MODE_STYLES[gameType] ?? FALLBACK_MODE_STYLE;
}

/** Modes that play on their own rule (year, country, colour, plot); shared cast is an opt-in modifier. */
export const STANDALONE_MODES = new Set([
  "chrono_climb",
  "historical_time_travel",
  "world_passport",
  "aesthetic_gradient",
  "semantic_trope",
  "genre_pendulum",
]);

/** Modes with no film-to-film graph at all (SQL trackers). */
export const TRACKER_MODES = new Set([
  "decade_sieve",
  "roulette",
  "march_madness",
  "method_actor",
  "auteur_marathon",
  "regional_deep_dive",
  "rt_split",
]);

/** Does this run link films through shared cast/directors? Drives the Pick Next layout. */
export function usesCastLinks(
  gameType: string,
  rules: Pick<RulesConfig, "require_cast_link" | "modifiers" | "link"> | null | undefined,
): boolean {
  if (gameType === "grid_crawler") {
    return !!rules?.link && rules.link !== "none";
  }
  if (STANDALONE_MODES.has(gameType)) {
    const nested = rules?.modifiers?.find((entry) => entry.key === "require_cast_link");
    return nested ? nested.params.enabled !== false : !!rules?.require_cast_link;
  }
  return !TRACKER_MODES.has(gameType);
}
