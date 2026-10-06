import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import RatingBadges from "./RatingBadges";
import AcquisitionControl from "./AcquisitionControl";
import MovieTagline from "./MovieTagline";
import ExpandableText from "./ui/ExpandableText";
import OnServerBadge from "./OnServerBadge";
import { CanonBadgeList } from "./CanonBadge";
import { api } from "../lib/api";
import { parseOriginCountries } from "../lib/countries";
import CountryFlags from "./CountryFlags";
import { useCanonBadgesBulk, useJellyfinLookup, useMovieDetail } from "../lib/queries";
import type { GenreOut } from "../types/api";

/** Lightweight, read-only movie preview - used for bridge path nodes (not yet
 * logged in any run, so the heavier step-editing MovieDetailModal doesn't apply). */
export default function MoviePreviewModal({
  open,
  onClose,
  movieId,
  fallbackTitle,
}: {
  open: boolean;
  onClose: () => void;
  movieId: number;
  fallbackTitle?: string;
}) {
  const { movie, isHydrating } = useMovieDetail(movieId, open);
  const { data: genres } = useQuery({
    queryKey: ["movies", "genres"],
    queryFn: () => api.get<GenreOut[]>("/movies/genres"),
    enabled: open,
  });
  const { data: jellyfinStatus } = useJellyfinLookup(open ? [movieId] : []);
  const { data: badgesMap } = useCanonBadgesBulk(open ? [movieId] : []);

  const countries = movie?.origin_countries ?? parseOriginCountries(movie?.origin_country);
  const genreNames = (movie?.genre_ids ?? [])
    .map((id) => genres?.find((g) => g.id === id)?.name)
    .filter((name): name is string => !!name);

  return (
    <Modal open={open} onClose={onClose} title={movie?.title ?? fallbackTitle ?? "Movie"} widthClassName="max-w-lg">
      <div className="flex gap-4">
        <MoviePoster path={movie?.poster_path ?? null} title={movie?.title ?? ""} className="w-28 shrink-0" />
        <div className="min-w-0 flex-1">
          {!movie ? (
            <div className="flex items-center gap-1.5 text-xs text-zinc-600">
              <Loader2 className="h-3 w-3 animate-spin" /> Loading details...
            </div>
          ) : (
            <>
              <p className="text-sm text-zinc-400">
                {movie.release_year ?? "—"}
                {movie.runtime ? ` · ${movie.runtime} min` : ""}
              </p>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <RatingBadges ratings={movie.ratings} movieId={movieId} />
                <OnServerBadge onServer={jellyfinStatus?.[String(movieId)]?.on_server} />
                <AcquisitionControl
                  tmdbId={movieId}
                  title={movie.title}
                  onServer={jellyfinStatus?.[String(movieId)]?.on_server}
                />
              </div>
              <div className="mt-1.5">
                <CanonBadgeList badges={badgesMap?.[String(movieId)]} />
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <CountryFlags codes={countries} />
                {genreNames.map((name) => (
                  <span key={name} className="rounded-full bg-app-surface-hover px-2 py-0.5 text-xs text-zinc-300">
                    {name}
                  </span>
                ))}
              </div>
              <MovieTagline tagline={movie.tagline} />
              <ExpandableText
                text={movie.overview}
                fallback={isHydrating ? "Fetching description..." : "No overview available."}
                lines={6}
                className="mt-2 text-xs text-zinc-500"
              />
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
