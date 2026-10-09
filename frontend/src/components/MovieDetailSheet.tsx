import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import MovieCastStrip from "./MovieCastStrip";
import RatingBadges from "./RatingBadges";
import AcquisitionControl from "./AcquisitionControl";
import MovieTagline from "./MovieTagline";
import ExpandableText from "./ui/ExpandableText";
import OnServerBadge from "./OnServerBadge";
import CountryFlags from "./CountryFlags";
import { CanonBadgeList } from "./CanonBadge";
import LinkBonusBadges from "./LinkBonusBadges";
import RoleBadge from "./RoleBadge";
import { stepCraftLink } from "../lib/crewRoles";
import { TropeChips, TropeLinkBadge } from "./TropeChips";
import HouseholdRatingModal from "./HouseholdRatingModal";
import { api } from "../lib/api";
import { parseOriginCountries } from "../lib/countries";
import { useLogFilm } from "../lib/useLogFilm";
import {
  useCanonBadgesBulk, useEngines, useJellyfinLookup, useMovieDetail as useMovieData,
  useMovieTropes, useRun, useUpdateStep,
} from "../lib/queries";
import { useMovieDetail, type MovieDetailOptions } from "../store/movieDetailStore";
import type {
  GenreOut,
  MovieTropeEvidence,
  RunDetail,
  RunStep,
  SplitCandidate,
} from "../types/api";

export function MovieDetailBody({ movieId, step, actions, onActorClick, runId }: MovieDetailOptions & { movieId: number }) {
  const { movie, isHydrating, error, refetch } = useMovieData(movieId);
  const { data: genres } = useQuery({
    queryKey: ["movies", "genres"],
    queryFn: () => api.get<GenreOut[]>("/movies/genres"),
  });
  const { data: jellyfin } = useJellyfinLookup([movieId]);
  const { data: badges } = useCanonBadgesBulk([movieId]);
  const { tropes, tropeEvidence, isExtracting } = useMovieTropes(movieId);
  const queryClient = useQueryClient();
  const [manualTrope, setManualTrope] = useState("");
  const [tropeError, setTropeError] = useState<string | null>(null);
  const addManualTrope = useMutation({
    mutationFn: () => {
      const tags = new Set([
        ...tropeEvidence.filter((item) => item.sources.includes("manual")).map((item) => item.slug),
        manualTrope.trim(),
      ]);
      return api.post<MovieTropeEvidence>(`/movies/${movieId}/tropes/manual`, {
        tropes: [...tags].filter(Boolean),
      });
    },
    onSuccess: (result) => {
      queryClient.setQueryData(["movies", movieId, "trope-evidence"], result);
      setManualTrope("");
      setTropeError(null);
    },
    onError: (error) => {
      setTropeError(error instanceof Error ? error.message : "Could not add your trope.");
    },
  });
  const title = movie?.title ?? step?.movie_title ?? "Movie";
  const craft = step ? stepCraftLink(step) : null;
  const actorName = step?.transition_metadata?.actor_name;
  const linkName = craft?.name ?? (typeof actorName === "string" ? actorName : null);
  return (
    <div className="flex flex-col gap-4">
      {error && <p role="alert" className="text-xs text-amber-300">Could not load film details. <button onClick={() => void refetch()} className="underline">Retry</button></p>}
      <div className="flex gap-4">
        <MoviePoster movieId={movieId} path={movie?.poster_path ?? step?.movie_poster_path ?? null}
          detailOptions={{ step, actions, onActorClick, runId }}
          title={title} className="w-24 shrink-0 self-start" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-zinc-100">{title}</p>
          <p className="text-xs text-zinc-400">
            {movie?.release_year ?? step?.movie_release_year ?? "—"}
            {movie?.runtime ? ` · ${movie.runtime} min` : ""}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <RatingBadges ratings={movie?.ratings} movieId={movieId} />
            <OnServerBadge onServer={jellyfin?.[String(movieId)]?.on_server} />
            <AcquisitionControl tmdbId={movieId} title={title} onServer={jellyfin?.[String(movieId)]?.on_server} />
          </div>
          <CanonBadgeList badges={badges?.[String(movieId)]} />
          <div className="mt-2 flex flex-wrap gap-1">
            <CountryFlags codes={movie?.origin_countries ?? step?.movie_origin_countries ?? parseOriginCountries(movie?.origin_country)} />
            {genres?.filter((genre) => movie?.genre_ids?.includes(genre.id)).map((genre) =>
              <span key={genre.id} className="rounded-full bg-app-surface-hover px-2 py-0.5 text-xs text-zinc-300">{genre.name}</span>)}
          </div>
          <MovieTagline tagline={movie?.tagline} />
          <ExpandableText text={movie?.overview} fallback={isHydrating ? "Fetching description..." : "No overview available."}
            lines={6} className="mt-2 text-xs text-zinc-400" />
        </div>
      </div>
      <TropeChips tropes={tropes} evidence={tropeEvidence} />
      {isExtracting && <p className="text-xs text-zinc-500">Finding themes...</p>}
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (manualTrope.trim()) addManualTrope.mutate();
        }}
      >
        <label className="flex flex-1 flex-col gap-1 text-[10px] text-zinc-500">
          Add a trope you confirm
          <input
            value={manualTrope}
            onChange={(event) => setManualTrope(event.target.value)}
            placeholder="e.g. TimeLoop"
            className="rounded border border-app-border bg-app-bg px-2 py-1.5 text-xs text-zinc-200"
          />
        </label>
        <button
          type="submit"
          disabled={!manualTrope.trim() || addManualTrope.isPending}
          className="rounded border border-app-border px-2.5 py-1.5 text-xs text-zinc-300 disabled:opacity-50"
        >
          Add as mine
        </button>
      </form>
      {tropeError && <p role="alert" className="text-xs text-amber-300">{tropeError}</p>}
      {step && <><TropeLinkBadge meta={step.transition_metadata} /><LinkBonusBadges meta={step.transition_metadata} /></>}
      {linkName && <section className="rounded border border-app-border p-3">
        <p className="text-xs text-zinc-500">Connected via</p>
        <p className="text-sm text-zinc-200">{linkName}</p>
        {craft && <RoleBadge role={craft.role} name={craft.name} fromRole={craft.fromRole} />}
        {typeof step?.transition_metadata?.character_in_from === "string" &&
          <p className="text-xs text-zinc-400">{step.transition_metadata.character_in_from} → {String(step.transition_metadata.character_in_to ?? "")}</p>}
      </section>}
      <section>
        <h3 className="text-xs font-semibold text-zinc-300">Cast</h3>
        <MovieCastStrip movieId={movieId} onActorClick={onActorClick} />
      </section>
      {actions}
    </div>
  );
}

function StepEditor({ run, step }: { run: RunDetail; step: RunStep }) {
  const update = useUpdateStep(run.id);
  const film = useLogFilm(run.id);
  const [notes, setNotes] = useState(step.user_notes ?? "");
  const [date, setDate] = useState((step.watched_at ?? new Date().toISOString()).slice(0, 10));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [rating, setRating] = useState<SplitCandidate | null>(null);
  const [retry, setRetry] = useState(false);
  const [checking, setChecking] = useState(false);
  const locked = run.status !== "active";
  const { data: engines } = useEngines();
  const canDelete = run.steps.at(-1)?.id === step.id || (step.status === "planned" &&
    engines?.find((engine) => engine.game_type === run.game_type)?.queue_policy === "slot");
  const busy = update.isPending || film.isPending || checking;
  const close = useMovieDetail((state) => state.close);
  async function watch() {
    setError(null);
    if (run.game_type !== "rt_split") {
      try { await film.markWatched(step, { watched_at: new Date(date).toISOString() }); }
      catch (error) { setError(error instanceof Error ? error.message : "Could not log watched."); }
      return;
    }
    setChecking(true);
    try {
      const result = await api.post<SplitCandidate | { qualifies: false; reason: string }>(`/runs/${run.id}/split/ratings/${step.movie_id}/retry`);
      if ("qualifies" in result) { setError(result.reason); setRetry(true); }
      else { setRating(result); setRetry(false); }
    } catch (error) { setError(error instanceof Error ? error.message : "Could not load ratings."); setRetry(true); }
    finally { setChecking(false); }
  }
  async function save() {
    setError(null); setSaved(false);
    try {
      await update.mutateAsync({ stepId: step.id, user_notes: notes.trim() || null,
        ...(step.status === "watched" ? { watched_at: new Date(date).toISOString() } : {}) });
      setSaved(true);
    } catch (error) { setError(error instanceof Error ? error.message : "Could not save changes."); }
  }
  return (
    <section aria-label="Your run entry" className="mt-4 flex flex-col gap-3 border-t border-app-border pt-4">
      <h3 className="text-sm font-semibold text-zinc-200">{step.status === "planned" ? "Up next" : "Watched"}</h3>
      <label className="text-xs text-zinc-400">Watched on
        <input type="date" value={date} max={new Date().toISOString().slice(0, 10)} required
          onChange={(event) => setDate(event.target.value)} className="mt-1 block rounded border border-app-border bg-app-bg p-2" />
      </label>
      <label className="text-xs text-zinc-400">Notes
        <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3}
          className="mt-1 block w-full rounded border border-app-border bg-app-bg p-2" />
      </label>
      <div className="flex flex-wrap gap-3 text-xs">
        <button type="button" disabled={busy || !date} onClick={() => void save()} className="text-accent">Save</button>
        {saved && <span role="status" className="text-emerald-400">Saved.</span>}
        {step.status === "planned" && !locked && <button type="button" disabled={busy || !date} onClick={() => void watch()} className="text-accent">Log watched</button>}
        {canDelete && !locked && <button type="button" disabled={busy} className="text-red-300" onClick={() => {
          if (!window.confirm(step.status === "planned" ? "Unqueue this film?" : "Delete this run entry and undo its progress?")) return;
          void film.unqueue(step).then(close).catch((error: Error) => setError(error.message));
        }}>{step.status === "planned" ? "Unqueue" : "Delete entry"}</button>}
      </div>
      {checking && <Loader2 aria-label="Checking ratings" className="h-4 w-4 animate-spin" />}
      {error && <p role="alert" className="text-xs text-amber-300">{error}</p>}
      {retry && !locked && <div className="flex gap-3 text-xs">
        <button disabled={busy} onClick={() => void watch()}>Retry ratings</button>
        <button disabled={busy} onClick={() => void film.markWatched(step, { no_contest: true, watched_at: new Date(date).toISOString() })
          .then(() => setRetry(false)).catch((error: Error) => setError(error.message))}>Log watched · no-contest</button>
      </div>}
      <HouseholdRatingModal runId={run.id} step={step} film={rating} watchedAt={date ? new Date(date).toISOString() : undefined} onClose={() => setRating(null)}
        onRatingsFailed={(_film, reason) => { setError(reason); setRating(null); setRetry(true); }} />
    </section>
  );
}

export default function MovieDetailSheet() {
  const { movieId, options, close } = useMovieDetail();
  const { data: run, error, refetch } = useRun(movieId !== null ? options.runId ?? options.step?.run_id : undefined);
  const step = run ? run.steps.find((entry) => entry.id === options.step?.id) : options.step;
  if (movieId === null) return null;
  return <Modal open onClose={close} title={step?.movie_title ?? "Film details"} widthClassName="max-w-xl">
    <MovieDetailBody key={movieId} movieId={movieId} {...options} step={step}
      onActorClick={options.onActorClick ? (actor) => { close(); options.onActorClick?.(actor); } : undefined} />
    {error && <p role="alert" className="mt-3 text-xs text-amber-300">Could not load the run entry. <button onClick={() => void refetch()} className="underline">Retry</button></p>}
    {run && step && <StepEditor key={`${step.id}:${step.status}`} run={run} step={step} />}
  </Modal>;
}
