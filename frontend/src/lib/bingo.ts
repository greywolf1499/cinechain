import type { BingoFilm } from "../types/api";

export interface BingoSquare {
  id: string;
  label: string;
  /** What qualifies, shown when the square is opened. */
  hint: string;
  test: (film: BingoFilm) => boolean;
}

export const FREE_SQUARE = "free";
export const BOARD_SIZE = 5;
const FREE_INDEX = 12;

const THIS_YEAR = new Date().getFullYear();
const ASIA = ["JP", "KR", "CN", "HK", "TW", "TH", "IN", "VN", "PH", "ID", "IR", "TR"];
const EUROPE = ["FR", "IT", "DE", "ES", "SE", "DK", "NO", "FI", "PL", "RU", "NL", "BE", "AT", "CH", "GR", "PT", "HU", "CZ", "RO", "IE"];
const LATAM_AFRICA = ["MX", "AR", "BR", "CL", "CO", "PE", "CU", "UY", "NG", "SN", "ZA", "EG", "MA", "DZ", "TN", "GH", "ML", "ET"];

const hasGenre = (film: BingoFilm, id: number) => film.genre_ids.includes(id);
const fromAny = (film: BingoFilm, codes: string[]) => film.origin_countries.some((c) => codes.includes(c));

function genreSquare(id: string, genreId: number, label: string): BingoSquare {
  return { id, label, hint: `A ${label.toLowerCase()} film`, test: (f) => hasGenre(f, genreId) };
}

function decadeSquare(decade: number): BingoSquare {
  return {
    id: `decade-${decade}`,
    label: `Decade: ${decade}s`,
    hint: `Released between ${decade} and ${decade + 9}`,
    test: (f) => f.year !== null && f.year >= decade && f.year < decade + 10,
  };
}

export const BINGO_SQUARES: BingoSquare[] = [
  { id: "classic", label: "Pre-1970 Classic", hint: "Released before 1970", test: (f) => f.year !== null && f.year < 1970 },
  { id: "short", label: "Runtime Under 85m", hint: "Shorter than 85 minutes", test: (f) => f.runtime !== null && f.runtime < 85 },
  { id: "epic", label: "Epic: 150+ Minutes", hint: "At least 150 minutes long", test: (f) => f.runtime !== null && f.runtime >= 150 },
  { id: "non-english", label: "Non-English Language", hint: "Original language isn't English", test: (f) => !!f.original_language && f.original_language !== "en" },
  { id: "imdb-high", label: "IMDb > 8.0", hint: "Rated above 8.0 on IMDb", test: (f) => f.imdb_rating !== null && f.imdb_rating > 8 },
  { id: "imdb-low", label: "IMDb Under 5.5", hint: "So bad it's good: rated under 5.5", test: (f) => f.imdb_rating !== null && f.imdb_rating < 5.5 },
  { id: "woman-director", label: "Directed by a Woman", hint: "At least one woman directed it", test: (f) => f.directed_by_woman === true },
  { id: "canon", label: "Sight & Sound / Canon Film", hint: "On one of your curated canon lists", test: (f) => f.canon_badges.length > 0 },
  { id: "recent", label: "Released in the Last 5 Years", hint: `Released ${THIS_YEAR - 5} or later`, test: (f) => f.year !== null && f.year >= THIS_YEAR - 5 },
  { id: "hidden-gem", label: "Hidden Gem", hint: "Low TMDB popularity - hardly anyone's seen it", test: (f) => f.popularity !== null && f.popularity < 8 },
  { id: "asian", label: "Asian Cinema", hint: "Made in Asia", test: (f) => fromAny(f, ASIA) },
  { id: "european", label: "European Cinema", hint: "Made in Europe", test: (f) => fromAny(f, EUROPE) },
  { id: "latam-africa", label: "Latin American or African Cinema", hint: "Made in Latin America or Africa", test: (f) => fromAny(f, LATAM_AFRICA) },
  { id: "not-us-uk", label: "Made Outside the US & UK", hint: "Neither American nor British", test: (f) => f.origin_countries.length > 0 && !fromAny(f, ["US", "GB"]) },
  genreSquare("documentary", 99, "Documentary"),
  genreSquare("animation", 16, "Animation"),
  genreSquare("horror", 27, "Horror"),
  genreSquare("scifi", 878, "Sci-Fi"),
  genreSquare("comedy", 35, "Comedy"),
  genreSquare("romance", 10749, "Romance"),
  genreSquare("war", 10752, "War"),
  genreSquare("western", 37, "Western"),
  genreSquare("musical", 10402, "Musical"),
  genreSquare("thriller", 53, "Thriller"),
  genreSquare("crime", 80, "Crime"),
  genreSquare("fantasy", 14, "Fantasy"),
  genreSquare("mystery", 9648, "Mystery"),
  ...[1960, 1970, 1980, 1990, 2000, 2010, 2020].map(decadeSquare),
];

const SQUARES_BY_ID = new Map(BINGO_SQUARES.map((s) => [s.id, s]));

export function squareById(id: string): BingoSquare | undefined {
  return SQUARES_BY_ID.get(id);
}

export function matchingFilms(square: BingoSquare, films: BingoFilm[]): BingoFilm[] {
  return films.filter(square.test);
}

function shuffle<T>(items: T[], random: () => number): T[] {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

/** 25 square ids with a free centre. With `preferFillable`, at least ~15 squares are ones the
 * watchlist can actually fill, so a fresh board is playable rather than aspirational. */
export function generateBoard(
  films: BingoFilm[],
  preferFillable: boolean,
  random: () => number = Math.random,
): string[] {
  const needed = BOARD_SIZE * BOARD_SIZE - 1;
  const shuffled = shuffle(BINGO_SQUARES, random);
  let chosen: BingoSquare[];
  if (preferFillable && films.length > 0) {
    const fillable = shuffled.filter((s) => films.some(s.test));
    const rest = shuffled.filter((s) => !fillable.includes(s));
    chosen = [...fillable.slice(0, Math.min(15, fillable.length)), ...rest, ...fillable.slice(15)].slice(0, needed);
  } else {
    chosen = shuffled.slice(0, needed);
  }
  const ids = shuffle(chosen, random).map((s) => s.id);
  ids.splice(FREE_INDEX, 0, FREE_SQUARE);
  return ids;
}

export interface BingoBoardState {
  squares: string[];
  /** Indices of stamped squares (the free centre is always stamped). */
  stamped: number[];
}

export function newBoardState(squares: string[]): BingoBoardState {
  return { squares, stamped: [FREE_INDEX] };
}

export function toggleStamp(state: BingoBoardState, index: number): BingoBoardState {
  if (index === FREE_INDEX) return state;
  const stamped = state.stamped.includes(index)
    ? state.stamped.filter((i) => i !== index)
    : [...state.stamped, index];
  return { ...state, stamped };
}

const LINES: number[][] = (() => {
  const lines: number[][] = [];
  for (let i = 0; i < BOARD_SIZE; i++) {
    lines.push(Array.from({ length: BOARD_SIZE }, (_, j) => i * BOARD_SIZE + j)); // row
    lines.push(Array.from({ length: BOARD_SIZE }, (_, j) => j * BOARD_SIZE + i)); // column
  }
  lines.push(Array.from({ length: BOARD_SIZE }, (_, i) => i * BOARD_SIZE + i));
  lines.push(Array.from({ length: BOARD_SIZE }, (_, i) => i * BOARD_SIZE + (BOARD_SIZE - 1 - i)));
  return lines;
})();

/** Every completed row, column and diagonal (as square indices). */
export function completedLines(stamped: number[]): number[][] {
  const set = new Set(stamped);
  return LINES.filter((line) => line.every((i) => set.has(i)));
}

const storageKey = (userId: string | undefined) => `cinechain:bingo:${userId ?? "anon"}`;

/** A stored board, or null when none / unreadable / from an older square set. */
export function loadBoard(userId: string | undefined): BingoBoardState | null {
  try {
    const raw = localStorage.getItem(storageKey(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as BingoBoardState;
    const valid =
      Array.isArray(parsed.squares) &&
      parsed.squares.length === BOARD_SIZE * BOARD_SIZE &&
      parsed.squares.every((id) => id === FREE_SQUARE || SQUARES_BY_ID.has(id)) &&
      Array.isArray(parsed.stamped) &&
      parsed.stamped.every((i) => Number.isInteger(i) && i >= 0 && i < BOARD_SIZE * BOARD_SIZE);
    return valid ? { squares: parsed.squares, stamped: [...new Set([...parsed.stamped, FREE_INDEX])] } : null;
  } catch {
    return null;
  }
}

export function saveBoard(userId: string | undefined, state: BingoBoardState): void {
  try {
    localStorage.setItem(storageKey(userId), JSON.stringify(state));
  } catch {
    /* storage full or blocked: the board just won't persist */
  }
}

export { FREE_INDEX };
