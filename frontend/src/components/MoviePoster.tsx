import { useState } from "react";
import { Film } from "lucide-react";
import { posterUrl } from "../lib/tmdbImage";
import { cn } from "../lib/cn";
import { useMovieDetail, type MovieDetailOptions } from "../store/movieDetailStore";

export default function MoviePoster({
  path,
  title,
  className,
  movieId,
  concealed = false,
  detailOptions,
}: {
  path: string | null;
  title: string;
  className?: string;
  movieId?: number;
  concealed?: boolean;
  detailOptions?: MovieDetailOptions;
}) {
  const openDetail = useMovieDetail((state) => state.open);
  const url = posterUrl(path);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  const image = !url || failedUrl === url ? (
      <div
        className={cn(
          "flex aspect-[2/3] items-center justify-center rounded-md bg-app-surface-hover text-zinc-600",
          "w-full",
        )}
      >
        <Film className="h-6 w-6" />
      </div>
    ) : (
    <img
      src={url}
      alt={concealed ? "Hidden poster" : title}
      loading="lazy"
      decoding="async"
      onError={() => setFailedUrl(url)}
      className="aspect-[2/3] w-full rounded-md object-cover"
    />
  );
  if (movieId !== undefined && !concealed) {
    return (
      <button type="button" aria-label={`Details for ${title}`}
        onClick={(event) => {
          event.stopPropagation();
          openDetail(movieId, detailOptions);
        }}
        className={cn("block rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent", className)}>
        {image}
      </button>
    );
  }
  return <div className={className}>{image}</div>;
}
