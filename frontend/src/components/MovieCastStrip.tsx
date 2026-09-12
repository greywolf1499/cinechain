import { useQuery } from "@tanstack/react-query";
import { Loader2, User } from "lucide-react";
import { api } from "../lib/api";
import { profileUrl } from "../lib/tmdbImage";
import type { ActorClickPayload } from "./actorClickTypes";
import type { CastMember } from "../types/api";

export default function MovieCastStrip({
  movieId,
  onActorClick,
}: {
  movieId: number;
  onActorClick: (actor: ActorClickPayload) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["movies", movieId, "cast"],
    queryFn: () => api.get<CastMember[]>(`/movies/${movieId}/cast`),
  });

  if (isLoading) {
    return (
      <div className="mt-2 flex justify-center">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-zinc-600" />
      </div>
    );
  }

  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {data?.slice(0, 8).map((member) => (
        <button
          key={member.actor_id}
          type="button"
          title={member.name}
          onClick={() =>
            onActorClick({
              actorId: member.actor_id,
              actorName: member.name,
              profilePath: member.profile_path,
            })
          }
          className="overflow-hidden rounded-full border border-app-border transition-transform hover:scale-110 hover:border-accent"
        >
          {member.profile_path ? (
            <img
              src={profileUrl(member.profile_path) ?? undefined}
              alt={member.name}
              className="h-7 w-7 object-cover"
            />
          ) : (
            <div className="flex h-7 w-7 items-center justify-center bg-app-surface-hover text-zinc-500">
              <User className="h-3.5 w-3.5" />
            </div>
          )}
        </button>
      ))}
    </div>
  );
}
