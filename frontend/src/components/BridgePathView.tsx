import { ArrowRight, User } from "lucide-react";
import AcquisitionControl from "./AcquisitionControl";
import MoviePoster from "./MoviePoster";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import { cn } from "../lib/cn";
import { profileUrl } from "../lib/tmdbImage";
import type { BridgeNode, JellyfinItemSummary, SharedActorConnection } from "../types/api";

/** Renders `[Movie A] -> (Actor) -> [Movie B] -> ...` on a single horizontal rail.
 * Movie cards share one fixed width and the connectors reserve a poster-height
 * zone (same border/padding box as a card), so arrows and actor avatars always
 * sit on the posters' vertical midline regardless of title length or badges. */
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
    <ol className="flex w-max min-w-full items-start justify-center">
      {path.map((node, index) => {
        const onServer = onServerMap?.[node.movie_id]?.on_server;
        const connection = connections[index];
        return (
          <li key={`${node.movie_id}-${index}`} className="flex items-start">
            <div
              className={cn(
                "flex w-32 shrink-0 flex-col items-center rounded-lg border p-2 text-center",
                onServer ? onServerCardClass(true) : "border-transparent",
              )}
            >
              <button
                type="button"
                onClick={() => onMovieClick?.(node.movie_id)}
                disabled={!onMovieClick}
                title={node.title}
                className="flex w-full min-w-0 flex-col items-center transition-opacity hover:opacity-80 disabled:cursor-default disabled:hover:opacity-100"
              >
                <MoviePoster path={node.poster_path} title={node.title} className="w-28" />
                <span className="mt-1.5 line-clamp-2 min-h-[2lh] w-full break-words text-xs font-medium leading-tight text-zinc-200">
                  {node.title}
                </span>
              </button>
              <p className="mt-0.5 h-4 text-[11px] text-zinc-500">{node.release_year ?? ""}</p>
              <div className="mt-1 flex min-h-5 flex-col items-center gap-1">
                <OnServerBadge onServer={onServer} />
                <AcquisitionControl tmdbId={node.movie_id} title={node.title} onServer={onServer} />
              </div>
            </div>

            {index < path.length - 1 && (
              <div aria-hidden={!connection} className="flex w-36 shrink-0 border border-transparent p-2">
                {/* h-42 = the poster height (w-28 at 2:3), centering the connector on it */}
                <div className="flex h-42 w-full flex-col items-center justify-center gap-1.5">
                  <div className="flex w-full items-center text-zinc-600">
                    <span className="h-px flex-1 bg-current" />
                    {connection &&
                      (connection.profile_path ? (
                        <img
                          src={profileUrl(connection.profile_path) ?? undefined}
                          alt={connection.actor_name}
                          loading="lazy"
                          className="mx-1.5 h-11 w-11 shrink-0 rounded-full object-cover ring-1 ring-app-border"
                        />
                      ) : (
                        <span className="mx-1.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500 ring-1 ring-app-border">
                          <User className="h-5 w-5" />
                        </span>
                      ))}
                    <span className="h-px flex-1 bg-current" />
                    <ArrowRight className="-ml-1 h-4 w-4 shrink-0" />
                  </div>
                  {connection && (
                    <p
                      title={connection.actor_name}
                      className="line-clamp-2 w-full break-words text-center text-[11px] font-medium leading-tight text-zinc-400"
                    >
                      {connection.actor_name}
                    </p>
                  )}
                </div>
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
