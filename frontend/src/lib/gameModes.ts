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
  /** One-line hook shown under the name. */
  tagline: string;
  /** Short chips: how films link in this mode. */
  tags: string[];
  /** Optional progression chips shown in order under the tags (e.g. the Rabbit Hole's tiers). */
  progression?: string[];
  /** Tinted icon bubble + selected-card ring/glow. */
  bubble: string;
  ring: string;
  text: string;
}

export const GAME_MODE_STYLES: Record<string, GameModeStyle> = {
  cinechain: {
    icon: Network,
    tagline: "Six Degrees of Kevin Bacon",
    tags: ["Shared cast"],
    bubble: "bg-amber-500/15 text-amber-300",
    ring: "border-amber-400 shadow-[0_0_0_1px_rgba(251,191,36,0.6),0_8px_30px_-8px_rgba(251,191,36,0.35)]",
    text: "text-amber-300",
  },
  auteur_relay: {
    icon: Clapperboard,
    tagline: "Actor, director, actor, director...",
    tags: ["Shared cast", "Shared director"],
    bubble: "bg-orange-500/15 text-orange-300",
    ring: "border-orange-400 shadow-[0_0_0_1px_rgba(251,146,60,0.6),0_8px_30px_-8px_rgba(251,146,60,0.35)]",
    text: "text-orange-300",
  },
  meet_in_the_middle: {
    icon: Combine,
    tagline: "Two partners, one tunnel",
    tags: ["Shared cast", "Co-op", "Two seeds"],
    bubble: "bg-cyan-500/15 text-cyan-300",
    ring: "border-cyan-400 shadow-[0_0_0_1px_rgba(34,211,238,0.6),0_8px_30px_-8px_rgba(34,211,238,0.35)]",
    text: "text-cyan-300",
  },
  genre_pendulum: {
    icon: Drama,
    tagline: "The genre swings as you go",
    tags: ["Any film", "Genre cycle", "Genre overlap"],
    bubble: "bg-red-500/15 text-red-300",
    ring: "border-red-400 shadow-[0_0_0_1px_rgba(248,113,113,0.6),0_8px_30px_-8px_rgba(248,113,113,0.35)]",
    text: "text-red-300",
  },
  crew_craft: {
    icon: SwatchBook,
    tagline: "Follow the craft, not just the cast",
    tags: ["Cast or crew", "Composer", "Cinematographer", "Writer", "Director"],
    bubble: "bg-fuchsia-500/15 text-fuchsia-300",
    ring: "border-fuchsia-400 shadow-[0_0_0_1px_rgba(232,121,249,0.6),0_8px_30px_-8px_rgba(232,121,249,0.35)]",
    text: "text-fuchsia-300",
  },
  tug_of_war: {
    icon: Swords,
    tagline: "Pull the rope your way",
    tags: ["Shared cast", "Two teams", "Era or geography"],
    bubble: "bg-lime-500/15 text-lime-300",
    ring: "border-lime-400 shadow-[0_0_0_1px_rgba(163,230,53,0.6),0_8px_30px_-8px_rgba(163,230,53,0.35)]",
    text: "text-lime-300",
  },
  canon_island: {
    icon: Landmark,
    tagline: "Stay on the canon",
    tags: ["Shared cast", "One curated list"],
    bubble: "bg-emerald-500/15 text-emerald-300",
    ring: "border-emerald-400 shadow-[0_0_0_1px_rgba(52,211,153,0.6),0_8px_30px_-8px_rgba(52,211,153,0.35)]",
    text: "text-emerald-300",
  },
  decade_sieve: {
    icon: CalendarRange,
    tagline: "One decade, no escape",
    tags: ["Any film", "One decade"],
    bubble: "bg-sky-500/15 text-sky-300",
    ring: "border-sky-400 shadow-[0_0_0_1px_rgba(56,189,248,0.6),0_8px_30px_-8px_rgba(56,189,248,0.35)]",
    text: "text-sky-300",
  },
  roulette: {
    icon: Dices,
    tagline: "Let the wheel decide",
    tags: ["Random pick"],
    bubble: "bg-pink-500/15 text-pink-300",
    ring: "border-pink-400 shadow-[0_0_0_1px_rgba(244,114,182,0.6),0_8px_30px_-8px_rgba(244,114,182,0.35)]",
    text: "text-pink-300",
  },
  chrono_climb: {
    icon: Hourglass,
    tagline: "Climb or descend through time",
    tags: ["Any film", "Release year"],
    bubble: "bg-violet-500/15 text-violet-300",
    ring: "border-violet-400 shadow-[0_0_0_1px_rgba(167,139,250,0.6),0_8px_30px_-8px_rgba(167,139,250,0.35)]",
    text: "text-violet-300",
  },
  historical_time_travel: {
    icon: History,
    tagline: "Travel through the eras stories are set in",
    tags: ["Any film", "Setting year", "Forward / Backward"],
    bubble: "bg-blue-500/15 text-blue-300",
    ring: "border-blue-400 shadow-[0_0_0_1px_rgba(96,165,250,0.6),0_8px_30px_-8px_rgba(96,165,250,0.35)]",
    text: "text-blue-300",
  },
  world_passport: {
    icon: Globe2,
    tagline: "A new country every film",
    tags: ["Any film", "Country"],
    bubble: "bg-teal-500/15 text-teal-300",
    ring: "border-teal-400 shadow-[0_0_0_1px_rgba(45,212,191,0.6),0_8px_30px_-8px_rgba(45,212,191,0.35)]",
    text: "text-teal-300",
  },
  rabbit_hole: {
    icon: Skull,
    tagline: "Descend. Survive. Don't blink.",
    tags: ["Shared cast", "3 lives", "Rogue-like"],
    progression: ["Freefall", "Pre-2000", "Non-English", "Under 100 min", "B-movies"],
    bubble: "bg-red-500/15 text-red-300",
    ring: "border-red-400 shadow-[0_0_0_1px_rgba(248,113,113,0.6),0_8px_30px_-8px_rgba(248,113,113,0.35)]",
    text: "text-red-300",
  },
  march_madness: {
    icon: Trophy,
    tagline: "16 films enter, one is crowned",
    tags: ["Tournament", "Watchlist", "Partner voting"],
    progression: ["Round of 16", "Quarters", "Semis", "Finals", "Champion"],
    bubble: "bg-yellow-500/15 text-yellow-300",
    ring: "border-yellow-400 shadow-[0_0_0_1px_rgba(250,204,21,0.6),0_8px_30px_-8px_rgba(250,204,21,0.35)]",
    text: "text-yellow-300",
  },
  method_actor: {
    icon: VenetianMask,
    tagline: "One career, in order",
    tags: ["One actor", "Chronological", "Milestones"],
    progression: ["🐣 Debut", "🚀 Breakout", "🏆 Prestige Peak", "👑 Resurgence"],
    bubble: "bg-fuchsia-500/15 text-fuchsia-300",
    ring: "border-fuchsia-400 shadow-[0_0_0_1px_rgba(232,121,249,0.6),0_8px_30px_-8px_rgba(232,121,249,0.35)]",
    text: "text-fuchsia-300",
  },
  auteur_marathon: {
    icon: Clapperboard,
    tagline: "One director, every feature",
    tags: ["One director", "Release order", "Filmography"],
    progression: ["🎬 First Feature", "🎞️ The Middle Years", "🏁 Final Film"],
    bubble: "bg-teal-500/15 text-teal-300",
    ring: "border-teal-400 shadow-[0_0_0_1px_rgba(45,212,191,0.6),0_8px_30px_-8px_rgba(45,212,191,0.35)]",
    text: "text-teal-300",
  },
  regional_deep_dive: {
    icon: Compass,
    tagline: "Conquer one corner of the canon",
    tags: ["Canon list", "Country", "Decade"],
    progression: ["🧭 Pick a Slice", "🗺️ Cross Off the Board", "🚩 Conquer It"],
    bubble: "bg-lime-500/15 text-lime-300",
    ring: "border-lime-400 shadow-[0_0_0_1px_rgba(163,230,53,0.6),0_8px_30px_-8px_rgba(163,230,53,0.35)]",
    text: "text-lime-300",
  },
  rt_split: {
    icon: Popcorn,
    tagline: "🍅 Critics vs 🍿 Audience",
    tags: ["Tomatometer split", "Head to head", "Household rating"],
    progression: ["🍅 Critic points", "🍿 Audience points", "🏆 First to 3"],
    bubble: "bg-pink-500/15 text-pink-300",
    ring: "border-pink-400 shadow-[0_0_0_1px_rgba(244,114,182,0.6),0_8px_30px_-8px_rgba(244,114,182,0.35)]",
    text: "text-pink-300",
  },
  aesthetic_gradient: {
    icon: Palette,
    tagline: "Fade poster to poster",
    tags: ["Any film", "Poster colour"],
    bubble: "bg-rose-500/15 text-rose-300",
    ring: "border-rose-400 shadow-[0_0_0_1px_rgba(251,113,133,0.6),0_8px_30px_-8px_rgba(251,113,133,0.35)]",
    text: "text-rose-300",
  },
  semantic_trope: {
    icon: Sparkles,
    tagline: "Follow the plot, not the cast",
    tags: ["Any film", "Plot similarity"],
    bubble: "bg-indigo-500/15 text-indigo-300",
    ring: "border-indigo-400 shadow-[0_0_0_1px_rgba(129,140,248,0.6),0_8px_30px_-8px_rgba(129,140,248,0.35)]",
    text: "text-indigo-300",
  },
};

export const FALLBACK_MODE_STYLE: GameModeStyle = {
  icon: Compass,
  tagline: "A custom challenge",
  tags: [],
  bubble: "bg-zinc-500/15 text-zinc-300",
  ring: "border-zinc-400 shadow-[0_0_0_1px_rgba(161,161,170,0.6)]",
  text: "text-zinc-300",
};

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
  rules: Pick<RulesConfig, "require_cast_link"> | null | undefined,
): boolean {
  if (STANDALONE_MODES.has(gameType)) return !!rules?.require_cast_link;
  return !TRACKER_MODES.has(gameType);
}
