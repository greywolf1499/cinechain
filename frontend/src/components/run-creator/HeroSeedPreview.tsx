import MoviePoster from "../MoviePoster";
import SeedMoviePicker from "../SeedMoviePicker";
import RatingBadges from "../RatingBadges";
import MovieTagline from "../MovieTagline";
import OnServerBadge from "../OnServerBadge";
import ExpandableText from "../ui/ExpandableText";
import { useJellyfinLookup, useMovieDetail } from "../../lib/queries";
import { parseOriginCountries } from "../../lib/countries";
import CountryFlags from "../CountryFlags";
import type { MovieSummary, RulesConfig, RawRulesConfig } from "../../types/api";

export default function HeroSeedPreview({
  value,
  onChange,
  gameType,
  label,
  rules,
  excludeIds = [],
  allowedIds,
  enabled = true,
}: {
  value: MovieSummary | null;
  onChange: (movie: MovieSummary | null) => void;
  gameType: string;
  label: string;
  rules: RulesConfig | RawRulesConfig;
  excludeIds?: number[];
  allowedIds?: number[] | null;
  enabled?: boolean;
}) {
  const legal = enabled && (!value || (
    (allowedIds == null || allowedIds.includes(value.tmdb_id)) &&
    !excludeIds.includes(value.tmdb_id)
  ));
  const { movie, isHydrating } = useMovieDetail(value?.tmdb_id, legal);
  const { data: jellyfin } = useJellyfinLookup(value && legal ? [value.tmdb_id] : []);
  const details = movie ?? value;
  const originCountries = details?.origin_countries ?? parseOriginCountries(details?.origin_country);
  const onServer = value ? jellyfin?.[String(value.tmdb_id)]?.on_server : undefined;

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-app-border bg-app-bg/60 p-3" aria-label={label}>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">{label}</h3>
      {!legal ? (
        <div className="text-xs text-amber-400">
          <p>Choose valid mode settings and a seed from the eligible slice.</p>
          {value && <button type="button" onClick={() => onChange(null)} className="mt-2 underline">Remove ineligible seed</button>}
        </div>
      ) : !value ? (
        <div className="rounded-lg border border-dashed border-zinc-600 p-3">
          <p className="mb-2 text-xs text-zinc-500">Choose a seed film or get a recommendation.</p>
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
                {originCountries.length > 0 && <> · <CountryFlags codes={originCountries} /></>}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <RatingBadges ratings={movie?.ratings} movieId={value.tmdb_id} />
                <OnServerBadge onServer={onServer} />
              </div>
              {isHydrating && <p className="mt-2 text-[11px] text-zinc-500">Loading film details…</p>}
              <MovieTagline tagline={movie?.tagline} />
            </div>
          </div>
          {movie?.overview && <ExpandableText text={movie.overview} lines={5} className="text-xs leading-relaxed text-zinc-400" />}
        </>
      )}
      {legal && <SeedMoviePicker value={value} onChange={onChange} gameType={gameType} rules={rules} excludeIds={excludeIds} allowedIds={allowedIds} recommendFirst={!value} />}
    </section>
  );
}
