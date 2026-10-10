import type { BingoSquare } from "../types/api";

export type { BingoSquare };

export const FREE_SQUARE = "free";
export const BOARD_SIZE = 5;
const FREE_INDEX = 12;

function shuffle<T>(items: T[], random: () => number): T[] {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

/** 25 square ids (from the server's square definitions) with a free centre. With
 * `preferFillable`, at least ~15 squares are ones the watchlist can actually fill, so a fresh
 * board is playable rather than aspirational. */
export function generateBoard(
  squares: BingoSquare[],
  preferFillable: boolean,
  random: () => number = Math.random,
): string[] {
  const gridSquares = squares.length === BOARD_SIZE * BOARD_SIZE
    && squares.every((square) => /^\d+:\d+$/.test(square.id));
  if (gridSquares) {
    const ids = [...squares]
      .sort((a, b) => {
        const [aRow, aCol] = a.id.split(":").map(Number);
        const [bRow, bCol] = b.id.split(":").map(Number);
        return aRow - bRow || aCol - bCol;
      })
      .map((square) => square.id);
    ids[FREE_INDEX] = FREE_SQUARE;
    return ids;
  }
  const needed = BOARD_SIZE * BOARD_SIZE - 1;
  const shuffled = shuffle(squares, random);
  let chosen: BingoSquare[];
  if (preferFillable && squares.some((s) => s.matches.length > 0)) {
    const fillable = shuffled.filter((s) => s.matches.length > 0);
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
  /** Square index -> the watchlist film the server accepted for it. Every stamp except the
   * free centre has an entry; stamps saved without one are dropped on load. */
  films?: Record<string, number>;
}

export function newBoardState(squares: string[]): BingoBoardState {
  return { squares, stamped: [FREE_INDEX], films: {} };
}

/** Stamp a square with a film the server has already validated for it. */
export function stampSquare(state: BingoBoardState, index: number, movieId: number): BingoBoardState {
  if (index === FREE_INDEX) return state;
  return {
    ...state,
    stamped: state.stamped.includes(index) ? state.stamped : [...state.stamped, index],
    films: { ...state.films, [index]: movieId },
  };
}

export function unstampSquare(state: BingoBoardState, index: number): BingoBoardState {
  if (index === FREE_INDEX) return state;
  const films = { ...state.films };
  delete films[index];
  return { ...state, stamped: state.stamped.filter((i) => i !== index), films };
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
export function loadBoard(
  userId: string | undefined,
  squareIds: ReadonlySet<string>,
): BingoBoardState | null {
  try {
    const raw = localStorage.getItem(storageKey(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as BingoBoardState;
    const valid =
      Array.isArray(parsed.squares) &&
      parsed.squares.length === BOARD_SIZE * BOARD_SIZE &&
      parsed.squares.every((id) => id === FREE_SQUARE || squareIds.has(id)) &&
      Array.isArray(parsed.stamped) &&
      parsed.stamped.every((i) => Number.isInteger(i) && i >= 0 && i < BOARD_SIZE * BOARD_SIZE);
    if (!valid) return null;
    // Only stamps backed by a server-validated film survive; legacy unverified stamps are
    // migrated to un-stamped (the free centre is always stamped).
    const films: Record<string, number> = {};
    if (parsed.films && typeof parsed.films === "object") {
      for (const [index, movieId] of Object.entries(parsed.films)) {
        const i = Number(index);
        if (Number.isInteger(movieId) && i !== FREE_INDEX && parsed.stamped.includes(i)) films[index] = movieId;
      }
    }
    const stamped = [FREE_INDEX, ...parsed.stamped.filter((i) => i !== FREE_INDEX && String(i) in films)];
    return { squares: parsed.squares, stamped: [...new Set(stamped)], films };
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
