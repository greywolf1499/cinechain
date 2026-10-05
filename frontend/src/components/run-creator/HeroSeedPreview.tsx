import MoviePoster from "../MoviePoster";
import SeedMoviePicker from "../SeedMoviePicker";
import RatingBadges from "../RatingBadges";
import MovieTagline from "../MovieTagline";
import OnServerBadge from "../OnServerBadge";
import ExpandableText from "../ui/ExpandableText";
import { useJellyfinLookup, useMovieDetail } from "../../lib/queries";
import { isoToFlagEmoji } from "../../lib/countries";
import type { MovieSummary } from "../../types/api";

export default function HeroSeedPreview({
  value,
  onChange,
  gameType,
  label,
}: {
  value: MovieSummary | null;
  onChange: (movie: MovieSummary | null) => void;
  gameType: string;
  label: string;
}) {
  const { movie, isHydrating } = useMovieDetail(value?.tmdb_id);
  const { data: jellyfin } = useJellyfinLookup(value ? [value.tmdb_id] : []);
  const details = movie ?? value;
  const onServer = value ? jellyfin?.[String(value.tmdb_id)]?.on_server : undefined;

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-app-border bg-app-bg/60 p-3" aria-label={label}>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">{label}</h3>
      {!value ? (
        <div className="rounded-lg border border-dashed border-zinc-600 p-3">
          <p className="mb-2 text-xs text-zinc-500">Choose a seed film or get a recommendation.</p>
          <SeedMoviePicker value={value} onChange={onChange} gameType={gameType} recommendFirst />
        </div>
      ) : (
        <>
          <div className="flex gap-3">
            <MoviePoster path={details?.poster_path ?? null} title={value.title} className="w-24 shrink-0 shadow-lg" />
            <div className="min-w-0 flex-1">
              <p className="break-words text-base font-semibold text-zinc-100">{details?.title ?? value.title}</p>
              <p className="mt-1 text-xs text-zinc-500">
                {details?.release_year ?? value.release_year ?? "Year unknown"}
                {movie?.runtime ? ` · ${movie.runtime} min` : ""}
                {details?.origin_country && ` · ${isoToFlagEmoji(details.origin_country)} ${details.origin_country}`}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <RatingBadges ratings={movie?.ratings} />
                <OnServerBadge onServer={onServer} />
              </div>
              {isHydrating && <p className="mt-2 text-[11px] text-zinc-500">Loading film details…</p>}
              <MovieTagline tagline={movie?.tagline} />
            </div>
          </div>
          {movie?.overview && <ExpandableText text={movie.overview} lines={5} className="text-xs leading-relaxed text-zinc-400" />}
          <SeedMoviePicker value={value} onChange={onChange} gameType={gameType} />
        </>
      )}
    </section>
  );
}
