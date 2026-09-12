import { Film } from "lucide-react";
import { posterUrl } from "../lib/tmdbImage";
import { cn } from "../lib/cn";

export default function MoviePoster({
  path,
  title,
  className,
}: {
  path: string | null;
  title: string;
  className?: string;
}) {
  const url = posterUrl(path);

  if (!url) {
    return (
      <div
        className={cn(
          "flex aspect-[2/3] items-center justify-center rounded-md bg-app-surface-hover text-zinc-600",
          className,
        )}
      >
        <Film className="h-6 w-6" />
      </div>
    );
  }

  return (
    <img
      src={url}
      alt={title}
      loading="lazy"
      className={cn("aspect-[2/3] rounded-md object-cover", className)}
    />
  );
}
