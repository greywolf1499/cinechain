import { ArrowRight, Clapperboard, Shuffle, User } from "lucide-react";
import AcquisitionControl from "./AcquisitionControl";
import MoviePoster from "./MoviePoster";
import ClampedLabel from "./ui/ClampedLabel";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import { cn } from "../lib/cn";
import { profileUrl } from "../lib/tmdbImage";
import type { BridgeNode, JellyfinItemSummary, PathTag, SharedActorConnection } from "../types/api";

const TAG_STYLES: Record<string, string> = {
  canon_heavy: "border-amber-800/60 bg-amber-950/40 text-amber-300",
  multi_country: "border-sky-800/60 bg-sky-950/40 text-sky-300",
  epic_runtimes: "border-violet-800/60 bg-violet-950/40 text-violet-300",
};

/** Small highlight chips for a path ("Canon Heavy", "Multi-Country", ...); the detail is a tooltip. */
export function PathTagChips({ tags }: { tags: PathTag[] | undefined }) {
  if (!tags || tags.length === 0) return null;
  return (
    <ul aria-label="Path highlights" className="flex flex-wrap gap-1.5">
      {tags.map((tag) => (
        <li
          key={tag.key}
          title={tag.detail}
          className={cn(
            "flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
            TAG_STYLES[tag.key] ?? "border-app-border bg-app-surface-hover text-zinc-300",
          )}
        >
          <span aria-hidden>{tag.emoji}</span>
          {tag.label}
        </li>
      ))}
    </ul>
  );
}

/** Renders `[Movie A] -> (Actor) -> [Movie B] -> ...` on a single horizontal rail.
 * Movie cards share one fixed width and the connectors reserve a poster-height
 * zone (same border/padding box as a card), so arrows and actor avatars always
 * sit on the posters' vertical midline regardless of title length or badges. */
export default function BridgePathView({
  path,
  connections,
  onServerMap,
  onMovieClick,
  onSwapNode,
  swapIndex = null,
}: {
  path: BridgeNode[];
  connections: SharedActorConnection[];
  onServerMap?: Record<number, JellyfinItemSummary>;
  onMovieClick?: (movieId: number) => void;
  /** Shows a shuffle button on the in-between films; the index is the node's path position. */
  onSwapNode?: (index: number) => void;
  swapIndex?: number | null;
}) {
  return (
    <ol className="flex w-max min-w-full items-start justify-center">
      {path.map((node, index) => {
        const onServer = onServerMap?.[node.movie_id]?.on_server;
        const connection = connections[index];
        const canSwap =
          !!onSwapNode && index > 0 && index < path.length - 1 && !!connections[index - 1] && !!connection;
        return (
          <li key={`${node.movie_id}-${index}`} className="flex items-start">
            <div
              className={cn(
                "flex w-32 shrink-0 flex-col items-center rounded-lg border p-2 text-center",
                onServer ? onServerCardClass(true) : "border-transparent",
                swapIndex === index && "border-accent/60 bg-accent/5",
              )}
            >
              <div
                title={node.title}
                className="flex w-full min-w-0 flex-col items-center transition-opacity hover:opacity-80 disabled:cursor-default disabled:hover:opacity-100"
              >
                <MoviePoster movieId={node.movie_id} path={node.poster_path} title={node.title} className="w-28" />
                <button type="button" onClick={() => onMovieClick?.(node.movie_id)} disabled={!onMovieClick}>
                <ClampedLabel
                  text={node.title}
                  lines={2}
                  as="span"
                  className="mt-1.5 min-h-[2lh] w-full text-xs font-medium leading-tight text-zinc-200"
                />
                </button>
              </div>
              <p className="mt-0.5 h-4 text-[11px] text-zinc-500">{node.release_year ?? ""}</p>
              <div className="mt-1 flex min-h-5 flex-col items-center gap-1">
                {canSwap && (
                  <button
                    type="button"
                    onClick={() => onSwapNode?.(index)}
                    aria-pressed={swapIndex === index}
                    title="Swap for another film with the same two actors"
                    className={cn(
                      "flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors",
                      swapIndex === index
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-app-border text-zinc-400 hover:border-accent/50 hover:text-accent",
                    )}
                  >
                    <Shuffle className="h-3 w-3" />
                    Swap
                  </button>
                )}
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
                      (connection.kind === "director" ? (
                        <span className="mx-1.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent ring-1 ring-accent/40">
                          <Clapperboard className="h-5 w-5" />
                        </span>
                      ) : connection.profile_path ? (
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
                    <div className="w-full text-center">
                      <ClampedLabel
                        text={connection.actor_name}
                        lines={2}
                        as="p"
                        className="text-[11px] font-medium leading-tight text-zinc-400"
                      />
                      {connection.kind === "director" && (
                        <span className="block text-[10px] text-accent">Director</span>
                      )}
                    </div>
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
