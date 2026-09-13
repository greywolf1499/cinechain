import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import OnServerBadge from "./OnServerBadge";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { useCreateStep } from "../lib/queries";
import type { JellyfinItemSummary, MovieSummary } from "../types/api";

const GENRE_CHIPS = [
  { id: 28, name: "Action" },
  { id: 35, name: "Comedy" },
  { id: 18, name: "Drama" },
  { id: 27, name: "Horror" },
  { id: 10749, name: "Romance" },
  { id: 878, name: "Sci-Fi" },
];

export default function ForkInTheRoadModal({
  open,
  onClose,
  actorId,
  actorName,
  actorProfilePath = null,
  actorCharacterName = null,
  runId,
  onLogged,
}: {
  open: boolean;
  onClose: () => void;
  actorId: number;
  actorName: string;
  actorProfilePath?: string | null;
  actorCharacterName?: string | null;
  runId: string;
  onLogged?: () => void;
}) {
  const [decade, setDecade] = useState<number | null>(null);
  const [genreId, setGenreId] = useState<number | null>(null);
  const createStep = useCreateStep(runId);

  const { data: allCredits } = useQuery({
    queryKey: ["people", actorId, "credits", "all"],
    queryFn: () => api.get<MovieSummary[]>(`/people/${actorId}/credits`),
    enabled: open,
  });

  const decades = useMemo(() => {
    const found = new Set<number>();
    for (const movie of allCredits ?? []) {
      if (movie.release_year) found.add(Math.floor(movie.release_year / 10) * 10);
    }
    return [...found].sort((a, b) => b - a);
  }, [allCredits]);

  const { data: credits, isLoading } = useQuery({
    queryKey: ["people", actorId, "credits", decade, genreId],
    queryFn: () => {
      const params = new URLSearchParams();
      if (decade) params.set("decade", String(decade));
      if (genreId) params.set("genre_id", String(genreId));
      const qs = params.toString();
      return api.get<MovieSummary[]>(`/people/${actorId}/credits${qs ? `?${qs}` : ""}`);
    },
    enabled: open,
  });

  const tmdbIds = credits?.map((m) => m.tmdb_id) ?? [];
  const { data: jellyfinStatus } = useQuery({
    queryKey: ["jellyfin", "lookup", tmdbIds],
    queryFn: () =>
      api.post<Record<string, JellyfinItemSummary>>("/integrations/jellyfin/lookup", {
        tmdb_ids: tmdbIds,
      }),
    enabled: tmdbIds.length > 0,
  });

  async function handleLog(movie: MovieSummary) {
    await createStep.mutateAsync({
      movie_id: movie.tmdb_id,
      force: true,
      transition_metadata: {
        actor_id: actorId,
        actor_name: actorName,
        profile_path: actorProfilePath,
        character_in_from: actorCharacterName,
      },
    });
    onLogged?.();
    onClose();
  }

  return (
    <Modal open={open} onClose={onClose} title={`${actorName}'s filmography`} widthClassName="max-w-lg">
      <div className="mb-3 flex flex-wrap gap-1.5">
        <Chip active={decade === null} onClick={() => setDecade(null)}>
          All decades
        </Chip>
        {decades.map((d) => (
          <Chip key={d} active={decade === d} onClick={() => setDecade(decade === d ? null : d)}>
            {d}s
          </Chip>
        ))}
      </div>
      <div className="mb-4 flex flex-wrap gap-1.5">
        <Chip active={genreId === null} onClick={() => setGenreId(null)}>
          All genres
        </Chip>
        {GENRE_CHIPS.map((g) => (
          <Chip key={g.id} active={genreId === g.id} onClick={() => setGenreId(genreId === g.id ? null : g.id)}>
            {g.name}
          </Chip>
        ))}
      </div>

      {isLoading && (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-zinc-600" />
        </div>
      )}

      <div className="flex flex-col gap-2">
        {credits?.length === 0 && !isLoading && (
          <p className="py-6 text-center text-sm text-zinc-500">No films match these filters.</p>
        )}
        {credits?.map((movie) => (
          <div
            key={movie.tmdb_id}
            className="flex items-center gap-3 rounded-md border border-app-border bg-app-bg p-2"
          >
            <MoviePoster path={movie.poster_path} title={movie.title} className="w-10" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-zinc-100">
                {movie.title}
                {movie.release_year && (
                  <span className="ml-1.5 text-zinc-500">({movie.release_year})</span>
                )}
              </p>
              <div className="mt-1">
                <OnServerBadge onServer={jellyfinStatus?.[String(movie.tmdb_id)]?.on_server} />
              </div>
            </div>
            <button
              type="button"
              disabled={createStep.isPending}
              onClick={() => handleLog(movie)}
              className="shrink-0 rounded-md bg-accent px-2.5 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
            >
              Log as next
            </button>
          </div>
        ))}
      </div>
    </Modal>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "border-accent bg-accent/10 text-accent"
          : "border-app-border text-zinc-500 hover:text-zinc-200",
      )}
    >
      {children}
    </button>
  );
}
