import { Star } from "lucide-react";
import type { MovieRatings } from "../types/api";
import { useRefreshRatings } from "../lib/queries";

/** Small IMDb (star) + Rotten Tomatoes (tomato) badge pair - renders nothing
 * for whichever rating OMDb didn't have (or when ratings are null/unfetched). */
export default function RatingBadges({ ratings, movieId }: { ratings: MovieRatings | null | undefined; movieId?: number }) {
  const refresh = useRefreshRatings();
  if (!ratings) return null;
  if (!ratings.imdb_rating && !ratings.rotten_tomatoes && !ratings.metacritic) {
    return (
      <span className="inline-flex flex-col gap-1 text-[10px] text-zinc-500">
        <span>No critic scores{movieId !== undefined && <> · <button type="button" disabled={refresh.isPending} onClick={() => refresh.mutate(movieId)} className="underline disabled:opacity-50">{refresh.isPending ? "Retrying..." : "Retry"}</button></>}</span>
        {refresh.isError && <span role="alert" className="text-amber-400">{refresh.error.message}</span>}
      </span>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {ratings.imdb_rating && (
        <span className="flex items-center gap-1 rounded-full bg-app-surface-hover px-1.5 py-0.5 text-[10px] font-medium text-yellow-400">
          <Star className="h-2.5 w-2.5 fill-current" />
          {ratings.imdb_rating}
        </span>
      )}
      {ratings.rotten_tomatoes && (
        <span className="flex items-center gap-1 rounded-full bg-app-surface-hover px-1.5 py-0.5 text-[10px] font-medium text-red-400">
          🍅 {ratings.rotten_tomatoes}
        </span>
      )}
    </div>
  );
}
