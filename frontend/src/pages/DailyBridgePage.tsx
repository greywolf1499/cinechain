import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Check, Clapperboard, Copy, Flag, Loader2, Lock, Popcorn, Search, Target, Undo2, User } from "lucide-react";
import Breadcrumbs from "../components/Breadcrumbs";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";
import Modal from "../components/Modal";
import MoviePoster from "../components/MoviePoster";
import ClampedLabel from "../components/ui/ClampedLabel";
import { ApiError, api } from "../lib/api";
import { cn } from "../lib/cn";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { profileUrl } from "../lib/tmdbImage";
import {
  useConvertDailyToRun,
  useDailyPuzzle,
  useForfeitDaily,
  useUndoDailyHop,
  useValidateDailyHop,
} from "../lib/queries";
import type { DailyPuzzle, MovieSummary, PuzzleHop, SharedActorConnection } from "../types/api";

export default function DailyBridgePage() {
  const { data: puzzle, isLoading, error } = useDailyPuzzle();
  const [resultOpen, setResultOpen] = useState(false);

  return (
    <div>
      <Breadcrumbs items={[{ label: "Tools", to: "/tools" }, { label: "Daily Bridge" }]} />
      <PageHeading
        title={puzzle ? `Daily Bridge #${puzzle.puzzle_number}` : "Daily Bridge"}
        subtitle={
          puzzle
            ? `${puzzle.date} · Link the two films through shared cast or directors - the fewer hops, the better.`
            : "One puzzle a day: connect two famous films through shared cast or directors."
        }
      />

      {isLoading && (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-zinc-600" />
        </div>
      )}

      {error && (
        <EmptyState
          icon={Lock}
          title="Today's puzzle isn't ready"
          description={
            (error instanceof ApiError && puzzleMessage(error)) ||
            "Today's puzzle could not be loaded. Try again in a moment."
          }
        />
      )}

      {puzzle && (
        <Board puzzle={puzzle} resultOpen={resultOpen} onResultOpenChange={setResultOpen} />
      )}
    </div>
  );
}

function puzzleMessage(error: ApiError): string | null {
  const detail = (error.body as { detail?: { message?: unknown } } | null)?.detail;
  return typeof detail?.message === "string" ? detail.message : null;
}

function Board({
  puzzle,
  resultOpen,
  onResultOpenChange,
}: {
  puzzle: DailyPuzzle;
  resultOpen: boolean;
  onResultOpenChange: (open: boolean) => void;
}) {
  const { attempt } = puzzle;
  const finished = attempt.status === "solved" || attempt.status === "forfeited";
  const tip = attempt.chain.length > 0 ? attempt.chain[attempt.chain.length - 1].movie : puzzle.start_movie;
  const validate = useValidateDailyHop();
  const undo = useUndoDailyHop();
  const forfeit = useForfeitDaily();
  const [confirmGiveUp, setConfirmGiveUp] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const hopsOverPar = attempt.hops - puzzle.par_hops;

  async function propose(movie: MovieSummary) {
    setMessage(null);
    try {
      const result = await validate.mutateAsync({
        current_movie_id: tip.tmdb_id,
        next_movie_id: movie.tmdb_id,
      });
      if (!result.valid) setMessage(`${movie.title}: ${result.reason ?? "no link found"}`);
      else if (result.solved) onResultOpenChange(true);
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Could not check that hop.");
    }
  }

  async function giveUp() {
    setConfirmGiveUp(false);
    await forfeit.mutateAsync();
    onResultOpenChange(true);
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-app-border bg-app-surface px-5 py-3">
        <p className="text-sm font-semibold text-zinc-100" aria-label="Live stats">
          Hops: <span className="tabular-nums">{attempt.hops}</span>
          <span className="mx-2 text-zinc-600">|</span>
          Par: <span className="tabular-nums">{puzzle.par_hops}</span>
        </p>
        {attempt.status === "in_progress" && attempt.hops > 0 && (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[11px] font-medium",
              hopsOverPar > 0 ? "bg-amber-950 text-amber-300" : "bg-emerald-950 text-emerald-300",
            )}
          >
            {hopsOverPar > 0 ? `${hopsOverPar} over par` : `${puzzle.par_hops - attempt.hops} hops to par`}
          </span>
        )}
        {attempt.status === "solved" && (
          <span className="rounded-full bg-emerald-950 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">
            Solved
          </span>
        )}
        {attempt.status === "forfeited" && (
          <span className="rounded-full bg-red-950 px-2 py-0.5 text-[11px] font-semibold text-red-300">
            Forfeited
          </span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {finished ? (
            <button
              type="button"
              onClick={() => onResultOpenChange(true)}
              className="rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-app-surface-hover"
            >
              {attempt.status === "solved" ? "View results" : "View the optimal route"}
            </button>
          ) : (
            <>
              <button
                type="button"
                disabled={attempt.hops === 0 || undo.isPending}
                onClick={() => undo.mutate()}
                className="flex items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:opacity-40"
              >
                <Undo2 className="h-3.5 w-3.5" /> Undo
              </button>
              {confirmGiveUp ? (
                <span className="flex items-center gap-1.5 text-xs text-zinc-400">
                  Reveal the answer?
                  <button
                    type="button"
                    onClick={() => void giveUp()}
                    disabled={forfeit.isPending}
                    className="rounded-md bg-red-600 px-2.5 py-1.5 font-semibold text-white transition-colors hover:bg-red-500 disabled:opacity-60"
                  >
                    Yes, give up
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmGiveUp(false)}
                    className="rounded-md px-2 py-1.5 text-zinc-500 hover:text-zinc-200"
                  >
                    Keep playing
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmGiveUp(true)}
                  className="flex items-center gap-1.5 rounded-md border border-red-900/60 px-3 py-1.5 text-xs font-medium text-red-300 transition-colors hover:bg-red-950/40"
                >
                  <Flag className="h-3.5 w-3.5" /> Give Up
                </button>
              )}
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-[10rem_minmax(0,1fr)_10rem]">
        <FilmCard movie={puzzle.start_movie} label="Start" />
        <div className="order-last flex min-w-0 flex-col gap-2 md:order-none">
          {attempt.chain.length === 0 && !finished && (
            <p className="rounded-lg border border-dashed border-app-border px-4 py-3 text-center text-xs text-zinc-500">
              Your chain starts here. Propose a film that shares an actor or director with{" "}
              <strong className="text-zinc-300">{puzzle.start_movie.title}</strong>.
            </p>
          )}
          {attempt.chain.map((hop, index) => (
            <ChainStep key={hop.movie.tmdb_id} hop={hop} index={index + 1} isTarget={hop.movie.tmdb_id === puzzle.target_movie.tmdb_id} />
          ))}
          {!finished && (
            <HopSearch
              tipTitle={tip.title}
              pending={validate.isPending}
              message={message}
              onPick={(movie) => void propose(movie)}
            />
          )}
        </div>
        <FilmCard movie={puzzle.target_movie} label="Target" target />
      </div>

      {finished && (
        <ResultModal
          open={resultOpen}
          onClose={() => onResultOpenChange(false)}
          puzzle={puzzle}
        />
      )}
    </div>
  );
}

function FilmCard({ movie, label, target = false }: { movie: MovieSummary; label: string; target?: boolean }) {
  return (
    <div
      className={cn(
        "flex flex-row items-center gap-3 rounded-xl border bg-app-surface p-3 md:flex-col md:items-stretch",
        target ? "border-accent/50" : "border-emerald-700/50",
      )}
    >
      <MoviePoster path={movie.poster_path} title={movie.title} className="w-20 shrink-0 md:w-full" />
      <div className="min-w-0 text-left md:text-center">
        <p className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500 md:justify-center">
          {target ? <Target className="h-3 w-3 text-accent" /> : <Clapperboard className="h-3 w-3 text-emerald-400" />}
          {label}
        </p>
        <ClampedLabel
          text={movie.title}
          lines={2}
          as="p"
          className="text-sm font-medium text-zinc-100"
        />
        <p className="text-[11px] text-zinc-500">{movie.release_year ?? "—"}</p>
      </div>
    </div>
  );
}

/** "🎭 Actor Name" / "🎬 Director Name": who links a film to the one before it. */
function LinkBadge({ link, className }: { link: SharedActorConnection | null; className?: string }) {
  if (!link) return null;
  const director = link.kind === "director";
  const photo = link.profile_path ? profileUrl(link.profile_path) : null;
  return (
    <span
      title={
        director
          ? `Directed both films: ${link.actor_name}`
          : [link.actor_name, [link.character_in_from, link.character_in_to].filter(Boolean).join(" → ")]
              .filter(Boolean)
              .join(" · ")
      }
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        director ? "bg-accent/15 text-accent" : "bg-sky-950 text-sky-300",
        className,
      )}
    >
      {photo ? (
        <img src={photo} alt="" className="h-4 w-4 rounded-full object-cover" />
      ) : director ? (
        <Clapperboard className="h-3 w-3" />
      ) : (
        <User className="h-3 w-3" />
      )}
      <span className="truncate">
        {director ? "Director: " : ""}
        {link.actor_name}
      </span>
    </span>
  );
}

function ChainStep({ hop, index, isTarget }: { hop: PuzzleHop; index: number; isTarget: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 pl-3">
        <span className="h-4 w-px bg-app-border" />
        <LinkBadge link={hop.link} />
      </div>
      <div
        className={cn(
          "flex items-center gap-3 rounded-lg border bg-app-surface p-2",
          isTarget ? "border-accent/60" : "border-app-border",
        )}
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-app-surface-hover text-[11px] font-semibold tabular-nums text-zinc-300">
          {index}
        </span>
        <MoviePoster path={hop.movie.poster_path} title={hop.movie.title} className="w-9 shrink-0" />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-zinc-100">{hop.movie.title}</p>
          <p className="text-[11px] text-zinc-500">{hop.movie.release_year ?? "—"}</p>
        </div>
        {isTarget && <Target className="ml-auto h-4 w-4 shrink-0 text-accent" />}
      </div>
    </div>
  );
}

function HopSearch({
  tipTitle,
  pending,
  message,
  onPick,
}: {
  tipTitle: string;
  pending: boolean;
  message: string | null;
  onPick: (movie: MovieSummary) => void;
}) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 300);
  const { data, isFetching } = useQuery({
    queryKey: ["movies", "search", debounced],
    queryFn: () => api.get<{ results: MovieSummary[] }>(`/movies/search?q=${encodeURIComponent(debounced)}`),
    enabled: debounced.trim().length > 1,
  });

  return (
    <div className="flex flex-col gap-2 pt-1">
      <div className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2 focus-within:border-accent">
        <Search className="h-4 w-4 shrink-0 text-zinc-500" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Next film after ${tipTitle}...`}
          aria-label="Propose the next film"
          className="w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
        />
        {(isFetching || pending) && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-zinc-500" />}
      </div>

      {message && (
        <p role="alert" className="rounded-md border border-red-900/50 bg-red-950/20 px-3 py-2 text-xs text-red-300">
          {message}
        </p>
      )}

      {query.trim().length > 1 && data && data.results.length > 0 && (
        <ul className="flex max-h-72 flex-col overflow-y-auto rounded-md border border-app-border bg-app-surface">
          {data.results.slice(0, 8).map((movie) => (
            <li key={movie.tmdb_id}>
              <button
                type="button"
                disabled={pending}
                onClick={() => {
                  setQuery("");
                  onPick(movie);
                }}
                className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-app-surface-hover disabled:opacity-50"
              >
                <MoviePoster path={movie.poster_path} title={movie.title} className="w-8 shrink-0" />
                <span className="min-w-0 truncate text-sm text-zinc-100">{movie.title}</span>
                <span className="ml-auto shrink-0 text-xs text-zinc-500">{movie.release_year ?? ""}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ResultModal({
  open,
  onClose,
  puzzle,
}: {
  open: boolean;
  onClose: () => void;
  puzzle: DailyPuzzle;
}) {
  const navigate = useNavigate();
  const convert = useConvertDailyToRun();
  const [copied, setCopied] = useState(false);
  const [convertError, setConvertError] = useState<string | null>(null);
  const { attempt } = puzzle;
  const solved = attempt.status === "solved";

  async function copyShare() {
    if (!attempt.share_text) return;
    await navigator.clipboard.writeText(attempt.share_text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  async function queueRun() {
    setConvertError(null);
    try {
      const result = await convert.mutateAsync();
      onClose();
      navigate(`/runs/${result.run_id}`);
    } catch (err) {
      setConvertError(err instanceof ApiError ? err.message : "Could not create the run.");
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={solved ? "🎉 Bridge complete!" : "🏳️ Puzzle forfeited"}
      widthClassName="max-w-lg"
    >
      <div className="flex flex-col gap-4">
        {solved ? (
          <>
            <p className="text-sm text-zinc-300">
              You linked <strong>{puzzle.start_movie.title}</strong> to{" "}
              <strong>{puzzle.target_movie.title}</strong> in{" "}
              <strong className="tabular-nums">{attempt.hops}</strong> hop{attempt.hops === 1 ? "" : "s"}{" "}
              <span className="text-zinc-500">(par {puzzle.par_hops})</span>.
              {attempt.hops <= puzzle.par_hops && " A perfect bridge!"}
            </p>
            {attempt.share_text && (
              <div className="flex flex-col gap-2">
                <pre
                  aria-label="Share grid"
                  className="whitespace-pre-wrap rounded-lg border border-app-border bg-app-bg p-3 text-sm leading-relaxed text-zinc-100"
                >
                  {attempt.share_text}
                </pre>
                <button
                  type="button"
                  onClick={() => void copyShare()}
                  className="flex w-fit items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-app-surface-hover"
                >
                  {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                  {copied ? "Copied!" : "Copy to share"}
                </button>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-zinc-300">
            The shortest route takes {puzzle.par_hops} hops. The Bridge Solver is unlocked again.
          </p>
        )}

        {puzzle.optimal_path && (
          <div>
            <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
              Optimal route ({puzzle.par_hops} hops)
            </p>
            <ol className="flex flex-col gap-1.5">
              {puzzle.optimal_path.map((hop, index) => (
                <li key={hop.movie.tmdb_id} className="flex flex-col gap-1">
                  {hop.link && <LinkBadge link={hop.link} className="ml-3" />}
                  <span className="flex items-center gap-2 text-sm text-zinc-200">
                    <span className="w-4 text-right text-[11px] tabular-nums text-zinc-500">{index}</span>
                    {hop.movie.title}
                    <span className="text-xs text-zinc-500">{hop.movie.release_year ?? ""}</span>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {convertError && <p className="text-xs text-red-300">{convertError}</p>}
        <button
          type="button"
          disabled={convert.isPending}
          onClick={() => void queueRun()}
          className="flex items-center justify-center gap-2 rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
        >
          {convert.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Popcorn className="h-4 w-4" />}
          [ 🍿 Queue as Challenge Run ]
        </button>
      </div>
    </Modal>
  );
}
