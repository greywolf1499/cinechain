import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Check, Loader2, Sparkles, Star } from "lucide-react";
import PageHeading from "../components/PageHeading";
import MoviePoster from "../components/MoviePoster";
import { ApiError, api } from "../lib/api";
import { cn } from "../lib/cn";
import {
  BOARD_SIZE,
  FREE_SQUARE,
  completedLines,
  generateBoard,
  loadBoard,
  newBoardState,
  saveBoard,
  stampSquare,
  unstampSquare,
  type BingoBoardState,
} from "../lib/bingo";
import { useAuthStore } from "../store/authStore";
import type { BingoFilm, BingoSquare, BingoSquares, BingoStampResult, BingoWatchlist } from "../types/api";

const HYDRATE_PER_ROUND = 10;
const MAX_HYDRATE_ROUNDS = 8;

/** Watchlist Bingo: a randomised 5x5 card of cinephile challenges, filled from your Letterboxd watchlist. */
export default function BingoPage() {
  const userId = useAuthStore((s) => s.user?.id);
  const [films, setFilms] = useState<BingoFilm[]>([]);
  const [squares, setSquares] = useState<BingoSquare[]>([]);
  const [pending, setPending] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [board, setBoard] = useState<BingoBoardState | null>(null);
  const boardOwner = useRef<string | undefined>(undefined);
  const [selected, setSelected] = useState<number | null>(null);
  const [preferFillable, setPreferFillable] = useState(true);
  const [stamping, setStamping] = useState<number | null>(null);
  const [stampError, setStampError] = useState<string | null>(null);

  // Read the server's squares and the watchlist, then top up missing details a few films at a
  // time, re-reading the squares' matches as details arrive.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [defs, first] = await Promise.all([
          api.get<BingoSquares>("/tools/bingo/grid").catch(() => api.get<BingoSquares>("/tools/bingo/squares")),
          api.get<BingoWatchlist>("/tools/bingo/watchlist"),
        ]);
        let data = first;
        if (cancelled) return;
        setSquares(defs.squares);
        setFilms(data.films);
        setPending(data.pending);
        setLoading(false);
        for (let round = 0; round < MAX_HYDRATE_ROUNDS && data.pending > 0; round++) {
          const before = data.pending;
          data = await api.get<BingoWatchlist>(`/tools/bingo/watchlist?hydrate=${HYDRATE_PER_ROUND}`);
          if (cancelled) return;
          setFilms(data.films);
          setPending(data.pending);
          if (data.pending >= before) break; // no progress (TMDB down, nothing fetchable)
          const refreshed = await api
            .get<BingoSquares>("/tools/bingo/grid")
            .catch(() => api.get<BingoSquares>("/tools/bingo/squares"));
          if (cancelled) return;
          setSquares(refreshed.squares);
        }
      } catch {
        if (!cancelled) {
          setLoadError("Couldn't read your Bingo squares or watchlist - try again in a moment.");
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  // Restore this user's saved board (the id arrives after the session loads), or deal the first one.
  useEffect(() => {
    if (loading || !userId || squares.length === 0 || boardOwner.current === userId) return;
    boardOwner.current = userId;
    const stored = loadBoard(userId, new Set(squares.map((s) => s.id)));
    if (stored) {
      setBoard(stored);
      saveBoard(userId, stored); // persist any legacy-stamp migration
      return;
    }
    const fresh = newBoardState(generateBoard(squares, preferFillable));
    setBoard(fresh);
    saveBoard(userId, fresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, userId, squares.length]);

  function update(next: BingoBoardState) {
    setBoard(next);
    saveBoard(userId, next);
  }

  function dealNewBoard() {
    update(newBoardState(generateBoard(squares, preferFillable)));
    setSelected(null);
    setStampError(null);
  }

  function select(index: number | null) {
    setSelected(index);
    setStampError(null);
  }

  /** Stamp only once the server confirms the chosen watchlist film fills the square. */
  async function stampWith(index: number, movieId: number) {
    if (!board) return;
    setStamping(movieId);
    setStampError(null);
    try {
      const result = await api.post<BingoStampResult>("/tools/bingo/stamp", {
        square_id: board.squares[index],
        movie_id: movieId,
      });
      if (result.valid) {
        setBoard((current) => {
          if (!current || current.squares[index] !== result.square_id) return current;
          const next = stampSquare(current, index, result.movie_id);
          saveBoard(userId, next);
          return next;
        });
      } else {
        setStampError(result.reason);
      }
    } catch (err) {
      setStampError(err instanceof ApiError ? err.message : "Couldn't check that film - try again.");
    } finally {
      setStamping(null);
    }
  }

  const squaresById = useMemo(() => new Map(squares.map((s) => [s.id, s])), [squares]);
  const filmsById = useMemo(() => new Map(films.map((f) => [f.movie_id, f])), [films]);

  const lines = useMemo(() => (board ? completedLines(board.stamped) : []), [board]);
  const lineCells = useMemo(() => new Set(lines.flat()), [lines]);
  const matches = useMemo(() => {
    const byIndex = new Map<number, BingoFilm[]>();
    board?.squares.forEach((id, index) => {
      const square = squaresById.get(id);
      if (square) {
        byIndex.set(
          index,
          square.matches.map((movieId) => filmsById.get(movieId)).filter((f): f is BingoFilm => f !== undefined),
        );
      }
    });
    return byIndex;
  }, [board, squaresById, filmsById]);

  const selectedSquare = board && selected !== null ? squaresById.get(board.squares[selected]) : undefined;
  const selectedFilms = selected !== null ? (matches.get(selected) ?? []) : [];
  const selectedStamped = board !== null && selected !== null && board.stamped.includes(selected);
  const stampedFilmId = board && selected !== null ? board.films?.[selected] : undefined;
  const stampedFilm = stampedFilmId !== undefined ? filmsById.get(stampedFilmId) : undefined;

  return (
    <div>
      <Link
        to="/tools"
        className="mb-3 inline-flex items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Tools
      </Link>
      <PageHeading
        title="Watchlist Bingo"
        subtitle="Click a square to see watchlist films that fill it, then stamp it with the one you watched."
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={dealNewBoard}
          disabled={loading}
          className="flex items-center gap-2 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
        >
          🎲 Generate New Board
        </button>
        <label className="flex items-center gap-1.5 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={preferFillable}
            onChange={(e) => setPreferFillable(e.target.checked)}
            className="accent-accent"
          />
          Favour squares my watchlist can fill
        </label>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-zinc-500" role="status">
          {(loading || pending > 0) && <Loader2 className="h-3 w-3 animate-spin" />}
          {loading
            ? "Reading your watchlist..."
            : films.length === 0
              ? "No synced watchlist yet"
              : pending > 0
                ? `${films.length} watchlist films - reading details for ${pending} more...`
                : `${films.length} watchlist ${films.length === 1 ? "film" : "films"}`}
        </span>
      </div>

      {loadError && <p className="mb-3 text-xs text-amber-400">{loadError}</p>}
      {!loading && !loadError && films.length === 0 && (
        <p className="mb-3 rounded-md border border-app-border bg-app-surface px-3 py-2 text-xs text-zinc-400">
          Sync your Letterboxd watchlist from Settings and the squares will suggest films to watch.
        </p>
      )}

      {lines.length > 0 && (
        <div
          role="status"
          className="mb-4 flex items-center gap-2 rounded-lg border border-accent/50 bg-accent/10 px-4 py-3 text-sm font-semibold text-accent"
        >
          <Sparkles className="h-4 w-4" />
          BINGO! {lines.length} {lines.length === 1 ? "line" : "lines"} complete
        </div>
      )}

      {board && (
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div
            role="grid"
            aria-label="Bingo board"
            className="grid gap-1.5"
            style={{ gridTemplateColumns: `repeat(${BOARD_SIZE}, minmax(0, 1fr))` }}
          >
            {board.squares.map((id, index) => {
              const square = squaresById.get(id);
              const isFree = id === FREE_SQUARE;
              const stamped = board.stamped.includes(index);
              const count = matches.get(index)?.length ?? 0;
              return (
                <div
                  key={`${index}-${id}`}
                  role="gridcell"
                  className={cn(
                    "relative aspect-square overflow-hidden rounded-lg border text-center transition-colors",
                    stamped ? "border-accent bg-accent/15" : "border-app-border bg-app-surface hover:border-zinc-600",
                    lineCells.has(index) && "ring-2 ring-accent",
                    selected === index && "outline outline-2 outline-offset-1 outline-sky-400",
                  )}
                >
                  <button
                    type="button"
                    disabled={isFree}
                    onClick={() => select(selected === index ? null : index)}
                    aria-label={isFree ? "Free space" : `${square?.label ?? id}${stamped ? " (stamped)" : ""}`}
                    className="flex h-full w-full flex-col items-center justify-center gap-1 p-1.5 disabled:cursor-default"
                  >
                    {isFree ? (
                      <Star className="h-6 w-6 fill-accent text-accent" />
                    ) : (
                      <>
                        <span
                          className={cn(
                            "text-[10px] font-semibold leading-tight sm:text-xs",
                            stamped ? "text-accent" : "text-zinc-200",
                          )}
                        >
                          {square?.label ?? id}
                        </span>
                        {count > 0 && (
                          <span className="rounded-full bg-app-surface-hover px-1.5 text-[9px] text-zinc-400">
                            {count} to watch
                          </span>
                        )}
                      </>
                    )}
                  </button>
                  {!isFree && (
                    <button
                      type="button"
                      onClick={() => (stamped ? update(unstampSquare(board, index)) : select(index))}
                      aria-label={stamped ? "Remove stamp" : "Choose a film to stamp this square"}
                      aria-pressed={stamped}
                      className={cn(
                        "absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full border transition-colors",
                        stamped
                          ? "border-accent bg-accent text-zinc-950"
                          : "border-app-border text-transparent hover:border-accent hover:text-accent/60",
                      )}
                    >
                      <Check className="h-3 w-3" strokeWidth={3} />
                    </button>
                  )}
                  {isFree && <span className="absolute inset-x-0 bottom-1 text-[9px] font-semibold text-accent">FREE</span>}
                </div>
              );
            })}
          </div>

          <aside
            aria-label="Square details"
            className="rounded-xl border border-app-border bg-app-surface p-4 lg:sticky lg:top-20"
          >
            {selectedSquare && selected !== null ? (
              <>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h2 className="text-sm font-semibold text-zinc-100">{selectedSquare.label}</h2>
                    <p className="text-xs text-zinc-500">{selectedSquare.hint}</p>
                  </div>
                  {selectedStamped && (
                    <button
                      type="button"
                      onClick={() => update(unstampSquare(board, selected))}
                      className="flex shrink-0 items-center gap-1 rounded-md border border-app-border px-2.5 py-1.5 text-xs font-semibold text-zinc-300 transition-colors hover:bg-app-surface-hover"
                    >
                      <Check className="h-3.5 w-3.5" />
                      Unstamp
                    </button>
                  )}
                </div>
                {selectedStamped && (
                  <p className="mt-2 text-xs text-accent">
                    Stamped{stampedFilm ? ` with ${stampedFilm.title}` : ""}.
                  </p>
                )}
                {stampError && (
                  <p role="alert" className="mt-2 text-xs text-amber-400">
                    {stampError}
                  </p>
                )}

                <h3 className="mb-2 mt-4 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                  From your watchlist ({selectedFilms.length})
                </h3>
                {selectedFilms.length === 0 ? (
                  <div className="text-xs text-zinc-500">
                    <p>
                      {films.length === 0
                        ? "Nothing on your watchlist yet."
                        : pending > 0 || (selectedSquare.unknown ?? 0) > 0
                          ? "Nothing matches yet - still reading film details."
                          : "No watchlist film fits this square - time to find one!"}
                    </p>
                    {films.length === 0 && (
                      <Link
                        to="/settings/integrations#watchlist"
                        className="mt-2 inline-block font-medium text-accent hover:underline"
                      >
                        Sync your watchlist
                      </Link>
                    )}
                  </div>
                ) : (
                  <ul className="flex max-h-96 flex-col gap-2 overflow-y-auto">
                    {selectedFilms.map((film) => (
                      <li key={film.movie_id} className="flex items-center gap-2.5 rounded-lg bg-app-bg p-2">
                        <MoviePoster movieId={film.movie_id} path={film.poster_path} title={film.title} className="w-9 shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-zinc-100">{film.title}</p>
                          <p className="text-[11px] text-zinc-500">
                            {[film.year, film.runtime ? `${film.runtime} min` : null, film.imdb_rating ? `IMDb ${film.imdb_rating}` : null]
                              .filter(Boolean)
                              .join(" · ")}
                          </p>
                        </div>
                        {!selectedStamped && (
                          <button
                            type="button"
                            disabled={stamping !== null}
                            onClick={() => void stampWith(selected, film.movie_id)}
                            aria-label={`Stamp with ${film.title}`}
                            className="flex shrink-0 items-center gap-1 rounded-md bg-accent px-2 py-1 text-[11px] font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
                          >
                            {stamping === film.movie_id ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <Check className="h-3 w-3" />
                            )}
                            Watched
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="text-xs text-zinc-500">
                Pick a square to see which of your watchlist films would fill it, then stamp it with
                the one you watched - the server checks the film really fits. Your board is saved in
                this browser.
              </p>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
