import { ArrowRight } from "lucide-react";
import AcquisitionControl from "./AcquisitionControl";
import MoviePoster from "./MoviePoster";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import { cn } from "../lib/cn";
import { profileUrl } from "../lib/tmdbImage";
import type { BridgeNode, JellyfinItemSummary, SharedActorConnection } from "../types/api";

export default function BridgePathView({
  path,
  connections,
  onServerMap,
  onMovieClick,
}: {
  path: BridgeNode[];
  connections: SharedActorConnection[];
  onServerMap?: Record<number, JellyfinItemSummary>;
  onMovieClick?: (movieId: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-start gap-1">
      {path.map((node, index) => (
        <div key={`${node.movie_id}-${index}`} className="flex items-start gap-1">
          <div
            className={cn(
              "w-24 rounded-lg border p-1 text-center",
              onServerMap?.[node.movie_id]?.on_server ? onServerCardClass(true) : "border-transparent",
            )}
          >
            <button
              type="button"
              onClick={() => onMovieClick?.(node.movie_id)}
              disabled={!onMovieClick}
              className="w-full text-left transition-opacity hover:opacity-80 disabled:cursor-default disabled:hover:opacity-100"
            >
              <MoviePoster path={node.poster_path} title={node.title} className="w-24" />
              <p className="mt-1.5 truncate text-xs font-medium text-zinc-200">{node.title}</p>
            </button>
            {node.release_year && <p className="text-[11px] text-zinc-500">{node.release_year}</p>}
            <div className="mt-1 flex flex-col items-center gap-1">
              <OnServerBadge onServer={onServerMap?.[node.movie_id]?.on_server} />
              <AcquisitionControl
                tmdbId={node.movie_id}
                title={node.title}
                onServer={onServerMap?.[node.movie_id]?.on_server}
              />
            </div>
          </div>

          {index < path.length - 1 && (
            <div className="flex flex-col items-center gap-1 px-1 pt-6">
              <ArrowRight className="h-4 w-4 shrink-0 text-zinc-600" />
              {connections[index] && (
                <div className="flex w-16 flex-col items-center text-center">
                  {connections[index].profile_path ? (
                    <img
                      src={profileUrl(connections[index].profile_path) ?? undefined}
                      alt={connections[index].actor_name}
                      className="h-8 w-8 rounded-full object-cover"
                    />
                  ) : (
                    <div className="h-8 w-8 rounded-full bg-app-surface-hover" />
                  )}
                  <p className="mt-1 line-clamp-2 text-[10px] leading-tight text-zinc-500">
                    {connections[index].actor_name}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
