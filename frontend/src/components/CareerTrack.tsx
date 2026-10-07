import { useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import MoviePoster from "./MoviePoster";
import QueuedFilmActions from "./QueuedFilmActions";
import { ApiError } from "../lib/api";
import { cn } from "../lib/cn";
import { MILESTONES, careerGroup, decadeOf, marathonPacing, trackStatuses } from "../lib/careerTrack";
import { useUpdateRunRules, useWrapMarathon } from "../lib/queries";
import { useLogFilm } from "../lib/useLogFilm";
import Popover from "./ui/Popover";
import type { AuteurFilm, CareerContext, CareerFilm, CareerMilestone, RunDetail } from "../types/api";

export function MarathonWrap({ run }: { run: RunDetail }) {
  const wrap = useWrapMarathon(run.id);
  const [confirming, setConfirming] = useState(false);
  if (run.status !== "active" || run.rules_config.track_length !== "endless") return null;
  const ids = new Set(run.rules_config.filmography?.map((film) => film.movie_id));
  const watched = new Set(run.steps.filter((step) => step.status === "watched" && ids.has(step.movie_id)).map((step) => step.movie_id)).size;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" disabled={wrap.isPending}
        onClick={() => confirming ? wrap.mutate() : setConfirming(true)}
        className="rounded-md border border-accent/50 px-3 py-2 text-xs text-accent disabled:opacity-50">
        {wrap.isPending ? "Wrapping…" : confirming ? `Confirm wrap · ${watched} of ${ids.size} watched` : "Wrap the marathon"}
      </button>
      {confirming && <button type="button" disabled={wrap.isPending} onClick={() => setConfirming(false)} className="text-xs text-zinc-400">Keep watching</button>}
      {wrap.isError && <p role="alert" className="text-xs text-red-300">{wrap.error instanceof ApiError ? wrap.error.message : "Could not wrap the marathon. Try again."}</p>}
    </div>
  );
}

/** The milestone pill: `[ 🐣 Debut ]`. */
export function MilestoneBadge({ milestone, evidence }: { milestone: CareerMilestone; evidence?: string }) {
  const info = MILESTONES[milestone];
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
    <button type="button" ref={anchorRef} aria-expanded={open} aria-label={`${info.label} evidence`}
      onMouseEnter={() => setOpen(true)} onFocus={() => setOpen(true)} onClick={() => setOpen(true)}
      className={cn(
        "rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ring-inset",
        info.className,
      )}
    >
      [ {info.emoji} {info.label} ]
    </button>
    <Popover anchorRef={anchorRef} open={open} onClose={() => setOpen(false)}
      label={`${info.label} evidence`} matchAnchorWidth={false} className="w-72 p-3 text-xs text-zinc-300">
      {evidence ?? "Legacy milestone: supporting evidence was not recorded on this run."}
    </Popover>
    </>
  );
}

export function MissingMilestones({ films, acting }: { films: CareerContext[]; acting: boolean }) {
  const recorded = new Set(films.flatMap((film) => film.milestones ?? []));
  const missing = [
    !recorded.has("prestige_peak") && "No prestige peak on record (too few ratings).",
    acting && !recorded.has("first_lead") && "No top-billed lead on this track.",
    !recorded.has("first_theatrical") && "First theatrical milestone not verified.",
    !recorded.has("genre_pivot") && "No sustained genre pivot on record.",
    !recorded.has("against_type") && "Against-type milestone not established (requires sufficient overview embeddings).",
  ].filter(Boolean);
  return <div className="space-y-1 text-xs text-zinc-500">{missing.map((text) => <p key={String(text)}>{text}</p>)}</div>;
}

export function CareerEraHeading({ run, track, index }: {
  run: RunDetail; track: (CareerFilm | AuteurFilm)[]; index: number;
}) {
  const group = careerGroup(track, index, run.rules_config);
  const startsGroup = index === 0 || careerGroup(track, index - 1, run.rules_config).key !== group.key;
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(group.annotation?.label ?? "");
  const [start, setStart] = useState(track[index].movie_id);
  let endIndex = index;
  while (endIndex + 1 < track.length && careerGroup(track, endIndex + 1, run.rules_config).key === group.key) endIndex++;
  const [end, setEnd] = useState(track[endIndex].movie_id);
  const update = useUpdateRunRules(run.id);
  const eras = run.rules_config.career_eras ?? [];
  const otherEras = eras.filter((era) => era !== group.annotation);
  if (!startsGroup) return null;
  function save(remove = false) {
    update.mutate({ career_eras: remove ? otherEras
      : [...otherEras, { start_movie_id: start, end_movie_id: end, label: label.trim() }] },
      { onSuccess: () => setEditing(false) });
  }
  function edit(initialLabel: string) {
    setLabel(initialLabel);
    setStart(track[index].movie_id);
    setEnd(track[endIndex].movie_id);
    update.reset();
    setEditing(true);
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <h3 className="min-w-0 break-words font-semibold text-zinc-400">{track[index].year}–{track[endIndex].year} · {group.label}</h3>
        {group.annotation && <span className="text-zinc-500">Player tag</span>}
        <button type="button" onClick={() => edit(group.annotation?.label ?? "")}
          className="text-accent">🏷️ Tag this era</button>
      </div>
      {track.slice(index, endIndex + 1).some((film) => film.suggestions?.includes("villain")) && (
        <div className="text-xs text-zinc-500">
          <p>Possible villain turn? From TMDB keywords; not a verified role.</p>
          {track.slice(index, endIndex + 1).filter((film) => film.suggestions?.includes("villain")).map((film) =>
            <p key={film.movie_id}>{film.title}: {film.suggestion_evidence}</p>)}
          <button type="button" onClick={() => edit("Villain era")}
            className="text-accent">Use as a player tag</button>
        </div>
      )}
      {editing && (
        <form className="flex flex-wrap items-end gap-2 rounded-md border border-app-border p-2 text-xs"
          onSubmit={(event) => { event.preventDefault(); save(); }}>
          <label className="flex flex-col gap-1">Era label
            <input value={label} maxLength={80} required onChange={(e) => setLabel(e.target.value)}
              className="rounded bg-app-bg p-2 text-zinc-200" />
          </label>
          <label className="flex min-w-0 flex-col gap-1">From
            <select value={start} onChange={(e) => setStart(Number(e.target.value))} className="max-w-48 rounded bg-app-bg p-2">
              {track.map((film) => <option key={film.movie_id} value={film.movie_id}>{film.year} · {film.title}</option>)}
            </select>
          </label>
          <label className="flex min-w-0 flex-col gap-1">Through
            <select value={end} onChange={(e) => setEnd(Number(e.target.value))} className="max-w-48 rounded bg-app-bg p-2">
              {track.map((film) => <option key={film.movie_id} value={film.movie_id}>{film.year} · {film.title}</option>)}
            </select>
          </label>
          <button type="submit" disabled={update.isPending || !label.trim()} className="rounded bg-accent px-3 py-2 text-zinc-950">Save player tag</button>
          {group.annotation && <button type="button" disabled={update.isPending} onClick={() => save(true)}
            className="text-zinc-400">Remove tag</button>}
          <button type="button" onClick={() => setEditing(false)} className="text-zinc-400">Cancel</button>
          {update.isError && <p role="alert" className="w-full text-red-300">{update.error instanceof ApiError ? update.error.message : "Could not save the era. Try again."}</p>}
        </form>
      )}
    </div>
  );
}

/** The Stage-2 affordances for a queued film on a board: log it watched, or take it back off
 * the queue. A finished run keeps the static label. */
export function QueuedFilm({
  run,
  movieId,
  locked,
  disabled,
  onError,
}: {
  run: RunDetail;
  movieId: number;
  locked: boolean;
  disabled?: boolean;
  onError: (message: string | null) => void;
}) {
  const step = run.steps.find((entry) => entry.movie_id === movieId && entry.status !== "watched");
  if (locked || step === undefined) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-[11px] font-medium text-sky-300">
        <Check className="h-3.5 w-3.5" /> Up next
      </span>
    );
  }
  return (
    <QueuedFilmActions runId={run.id} stepId={step.id} disabled={disabled} onError={onError} />
  );
}

/** The actor's career, oldest film first: age and decade at each film, milestone badges and the
 * log buttons that walk the marathon forward. */
export default function CareerTrack({ run }: { run: RunDetail }) {
  const track = (run.rules_config.filmography ?? []) as CareerFilm[];
  const actorName = run.rules_config.actor?.name ?? "the actor";
  const locked = run.status !== "active";
  const logFilm = useLogFilm(run.id);
  const pendingId = logFilm.pendingMovieId;
  const [message, setMessage] = useState<string | null>(null);
  const { statuses } = trackStatuses(track, run.steps);
  const watched = [...statuses.values()].filter((s) => s === "watched").length;


  return (
    <section aria-label="Career progression track" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-100">
          🎭 {actorName}: Career Progression Track
        </h2>
        <p className="text-xs text-zinc-500">
          {watched} of {track.length} watched · {marathonPacing(run.rules_config)}
        </p>
      </div>
      <MarathonWrap run={run} />
      <MissingMilestones films={track} acting />
      {message && (
        <p role="alert" className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
          {message}
        </p>
      )}
      <ol className="relative flex flex-col gap-3 border-l border-app-border pl-5">
        {track.map((film, index) => {
          const state = statuses.get(film.movie_id) ?? "upcoming";
          return (
            <li key={film.movie_id} className="flex flex-col gap-1.5">
              <CareerEraHeading run={run} track={track} index={index} />
              <div
                className={cn(
                  "relative flex flex-wrap items-center gap-3 rounded-xl border p-2.5 sm:flex-nowrap",
                  state === "watched" && "border-emerald-700/50 bg-emerald-950/20",
                  state === "planned" && "border-sky-800/50 bg-sky-950/20",
                  state === "next" && "border-accent/70 bg-app-surface shadow-[0_0_0_1px_rgba(251,191,36,0.25)]",
                  state === "upcoming" && "border-app-border bg-app-bg/60",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "absolute -left-[1.62rem] top-1/2 h-2.5 w-2.5 -translate-y-1/2 rounded-full ring-2 ring-app-bg",
                    state === "watched" ? "bg-emerald-400" : state === "next" ? "bg-accent" : "bg-zinc-700",
                  )}
                />
                <MoviePoster path={film.poster_path} title={film.title} className="w-12 shrink-0" />
                <div className="min-w-0 flex-1 basis-36 sm:basis-auto">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <p className="truncate text-sm font-medium text-zinc-100">{film.title}</p>
                    {film.milestones.map((milestone) => (
                      <MilestoneBadge key={milestone} milestone={milestone} evidence={film.evidence?.[milestone]} />
                    ))}
                  </div>
                  <p className="text-[11px] text-zinc-500">
                    {film.year} · {decadeOf(film.year)}
                    {film.age != null && ` · age ${film.age}`}
                    {film.character ? ` · as ${film.character}` : ""}
                  </p>
                </div>
                {state === "watched" ? (
                  <span className="flex shrink-0 items-center gap-1 text-[11px] font-medium text-emerald-300">
                    <Check className="h-3.5 w-3.5" /> Watched
                  </span>
                ) : state === "planned" ? (
                  <QueuedFilm
                    run={run}
                    movieId={film.movie_id}
                    locked={locked}
                    disabled={pendingId !== null}
                    onError={setMessage}
                  />
                ) : (
                  !locked && (
                    <div className="flex shrink-0 gap-1.5">
                      <button
                        type="button"
                        disabled={pendingId !== null}
                        onClick={() => void logFilm.queue(film.movie_id).catch((error: Error) => setMessage(error.message))}
                        className="rounded-md border border-app-border px-2.5 py-1.5 text-[11px] font-medium text-zinc-300 hover:bg-app-surface-hover disabled:opacity-50"
                      >
                        Queue
                      </button>
                      <button
                        type="button"
                        disabled={pendingId !== null}
                        onClick={() => void logFilm.logWatched(film.movie_id).catch((error: Error) => setMessage(error.message))}
                        className="flex items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-[11px] font-semibold text-zinc-950 hover:bg-accent-strong disabled:opacity-50"
                      >
                        {pendingId === film.movie_id && <Loader2 className="h-3 w-3 animate-spin" />}
                        Log watched
                      </button>
                    </div>
                  )
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
