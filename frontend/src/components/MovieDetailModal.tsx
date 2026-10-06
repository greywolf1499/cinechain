import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Loader2, Trash2, User } from "lucide-react";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import MovieCastStrip from "./MovieCastStrip";
import RatingBadges from "./RatingBadges";
import AcquisitionControl from "./AcquisitionControl";
import MovieTagline from "./MovieTagline";
import ExpandableText from "./ui/ExpandableText";
import LinkBonusBadges from "./LinkBonusBadges";
import RoleBadge from "./RoleBadge";
import { TropeChips, TropeLinkBadge } from "./TropeChips";
import { stepCraftLink } from "../lib/crewRoles";
import OnServerBadge from "./OnServerBadge";
import { CanonBadgeList } from "./CanonBadge";
import { api } from "../lib/api";
import { parseOriginCountries } from "../lib/countries";
import CountryFlags from "./CountryFlags";
import { profileUrl } from "../lib/tmdbImage";
import {
  useCanonBadgesBulk,
  useJellyfinLookup,
  useMovieDetail,
  useMovieTropes,
  useUpdateStep,
} from "../lib/queries";
import type { ActorClickPayload } from "./actorClickTypes";
import type { GenreOut, RunStep } from "../types/api";

interface TransitionMeta {
  actor_id?: number;
  actor_name?: string;
  profile_path?: string | null;
  character_in_from?: string | null;
  character_in_to?: string | null;
}

export default function MovieDetailModal({
  open,
  onClose,
  runId,
  step,
  isTailStep,
  onActorClick,
  onRequestMarkWatched,
  onRequestDelete,
  locked = false,
}: {
  open: boolean;
  onClose: () => void;
  runId: string;
  step: RunStep;
  isTailStep: boolean;
  onActorClick: (actor: ActorClickPayload) => void;
  onRequestMarkWatched: () => void;
  onRequestDelete: () => void;
  locked?: boolean;
}) {
  const { movie, isHydrating } = useMovieDetail(step.movie_id, open);
  const { data: genres } = useQuery({
    queryKey: ["movies", "genres"],
    queryFn: () => api.get<GenreOut[]>("/movies/genres"),
    enabled: open,
  });
  const { data: jellyfinStatus } = useJellyfinLookup(open ? [step.movie_id] : []);
  const { data: badgesMap } = useCanonBadgesBulk(open ? [step.movie_id] : []);
  const { tropes, isExtracting } = useMovieTropes(step.movie_id, open);

  const updateStep = useUpdateStep(runId);
  const [notes, setNotes] = useState(step.user_notes ?? "");
  const [watchedDate, setWatchedDate] = useState(() =>
    step.watched_at ? step.watched_at.slice(0, 10) : new Date().toISOString().slice(0, 10),
  );
  const [saved, setSaved] = useState(false);

  // Reset local edit state whenever a different station is opened.
  useEffect(() => {
    setNotes(step.user_notes ?? "");
    setWatchedDate(step.watched_at ? step.watched_at.slice(0, 10) : new Date().toISOString().slice(0, 10));
    setSaved(false);
  }, [step.id, step.user_notes, step.watched_at]);

  const meta = step.transition_metadata as TransitionMeta | null;
  const craft = stepCraftLink(step);
  const linkName = craft?.name ?? meta?.actor_name ?? "";
  const countries = movie?.origin_countries ?? step.movie_origin_countries ?? parseOriginCountries(movie?.origin_country ?? step.movie_origin_country);
  const genreNames = (movie?.genre_ids ?? [])
    .map((id) => genres?.find((g) => g.id === id)?.name)
    .filter((name): name is string => !!name);
  const characters = [meta?.character_in_from, meta?.character_in_to].filter(Boolean).join(" → ");

  async function handleSave() {
    setSaved(false);
    await updateStep.mutateAsync({
      stepId: step.id,
      user_notes: notes.trim() || null,
      ...(step.status === "watched" ? { watched_at: new Date(watchedDate).toISOString() } : {}),
    });
    setSaved(true);
  }

  return (
    <Modal open={open} onClose={onClose} title={step.movie_title} widthClassName="max-w-xl">
      <div className="flex flex-col gap-4">
        <div className="flex gap-4">
          <MoviePoster path={step.movie_poster_path} title={step.movie_title} className="w-28 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-sm text-zinc-400">
              {step.movie_release_year ?? "—"}
              {movie?.runtime ? ` · ${movie.runtime} min` : ""}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <RatingBadges ratings={movie?.ratings} movieId={step.movie_id} />
              <OnServerBadge onServer={jellyfinStatus?.[String(step.movie_id)]?.on_server} />
              <AcquisitionControl
                tmdbId={step.movie_id}
                title={step.movie_title}
                onServer={jellyfinStatus?.[String(step.movie_id)]?.on_server}
              />
            </div>
            <div className="mt-1.5">
              <CanonBadgeList badges={badgesMap?.[String(step.movie_id)]} />
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <CountryFlags codes={countries} />
              {genreNames.map((name) => (
                <span key={name} className="rounded-full bg-app-surface-hover px-2 py-0.5 text-xs text-zinc-300">
                  {name}
                </span>
              ))}
            </div>
            {(tropes.length > 0 || isExtracting) && (
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <TropeChips tropes={tropes} className="[&>span]:text-[10px]" />
                {isExtracting && (
                  <span className="flex items-center gap-1 text-[10px] text-zinc-600">
                    <Loader2 className="h-3 w-3 animate-spin" /> Extracting tropes...
                  </span>
                )}
              </div>
            )}
            {movie ? (
              <>
              <MovieTagline tagline={movie.tagline} />
              <ExpandableText
                text={movie.overview}
                fallback={isHydrating ? "Fetching description..." : "No overview available."}
                lines={6}
                className="mt-2 text-xs text-zinc-500"
              />
              </>
            ) : (
              <div className="mt-2 flex items-center gap-1.5 text-xs text-zinc-600">
                <Loader2 className="h-3 w-3 animate-spin" /> Loading details...
              </div>
            )}
          </div>
        </div>

        <TropeLinkBadge meta={step.transition_metadata} />

        {(meta?.actor_name || craft) && (
          <div className="flex items-center gap-2.5 rounded-md border border-app-border bg-app-bg p-2.5">
            {meta?.profile_path ? (
              <img
                src={profileUrl(meta.profile_path) ?? undefined}
                alt={linkName}
                className="h-9 w-9 shrink-0 rounded-full object-cover"
              />
            ) : (
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500">
                <User className="h-4 w-4" />
              </div>
            )}
            <div className="min-w-0">
              <p className="text-xs text-zinc-500">Connected via</p>
              <p className="truncate text-sm font-medium text-zinc-200">{linkName}</p>
              {craft && <RoleBadge role={craft.role} name={craft.name} fromRole={craft.fromRole} className="mt-0.5" />}
              {characters && <p className="truncate text-[11px] text-zinc-500">{characters}</p>}
              <LinkBonusBadges meta={step.transition_metadata} className="mt-1.5" />
            </div>
          </div>
        )}

        <div>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">Cast</p>
          <MovieCastStrip movieId={step.movie_id} onActorClick={onActorClick} />
        </div>

        <div className="flex flex-col gap-3 border-t border-app-border pt-3.5">
          {step.status === "watched" ? (
            <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
              Watched on
              <input
                type="date"
                value={watchedDate}
                max={new Date().toISOString().slice(0, 10)}
                onChange={(e) => setWatchedDate(e.target.value)}
                className="rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 focus:border-accent focus:outline-none"
              />
            </label>
          ) : locked ? null : (
            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={updateStep.isPending}
                onClick={() => updateStep.mutate({ stepId: step.id, watched_at: new Date().toISOString() })}
                className="flex items-center justify-center gap-1.5 rounded-md bg-accent/10 px-3 py-2 text-sm font-medium text-accent transition-colors hover:bg-accent/20 disabled:opacity-60"
              >
                {updateStep.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
                Mark as Watched
              </button>
              <button
                type="button"
                onClick={onRequestMarkWatched}
                className="text-xs text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline"
              >
                Backdate...
              </button>
            </div>
          )}

          <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
            Notes
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="Thoughts, memories..."
              className="resize-none rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none"
            />
          </label>

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={updateStep.isPending}
              onClick={handleSave}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
            >
              {updateStep.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Save
            </button>
            {saved && <span className="text-xs text-emerald-400">Saved.</span>}

            {isTailStep && !locked && (
              <button
                type="button"
                onClick={onRequestDelete}
                className="ml-auto flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium text-zinc-500 transition-colors hover:text-red-400"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete Step
              </button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
