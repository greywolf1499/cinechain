import { Clapperboard, Star, User } from "lucide-react";
import { profileUrl } from "../lib/tmdbImage";
import type { RunStep } from "../types/api";

interface TransitionMeta {
  connection_type?: "actor" | "director";
  director_id?: number;
  director_name?: string;
  actor_id?: number;
  actor_name?: string;
  profile_path?: string | null;
  character_in_from?: string | null;
  character_in_to?: string | null;
}

/** Renders on the vertical spine track between two station cards. */
export default function ChainLink({ step, isKeystone }: { step: RunStep; isKeystone: boolean }) {
  const meta = step.transition_metadata as TransitionMeta | null;
  const isDirector = meta?.connection_type === "director";
  const actorName = isDirector ? meta?.director_name : meta?.actor_name;
  const photo = meta?.profile_path ? profileUrl(meta.profile_path) : null;
  const characters = [meta?.character_in_from, meta?.character_in_to].filter(Boolean).join(" → ");

  return (
    <div className="relative flex items-center gap-3 py-1">
      <div className="flex w-8 shrink-0 items-center justify-center">
        <span className="h-2 w-2 rounded-full bg-app-border" />
      </div>
      <div className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg border border-dashed border-app-border bg-app-bg/60 px-2.5 py-1.5">
        {actorName ? (
          <>
            {isDirector ? (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent">
                <Clapperboard className="h-4 w-4" />
              </div>
            ) : photo ? (
              <img src={photo} alt={actorName} className="h-8 w-8 shrink-0 rounded-full object-cover" />
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500">
                <User className="h-4 w-4" />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <p className="truncate text-xs font-medium text-zinc-300">{actorName}</p>
                {isDirector && (
                  <span className="shrink-0 rounded-full bg-accent/15 px-1.5 py-0.5 text-[9px] font-semibold text-accent">
                    Director
                  </span>
                )}
                {isKeystone && !isDirector && (
                  <span
                    title="Keystone actor - connects this run multiple times"
                    className="flex shrink-0 items-center gap-0.5 rounded-full bg-accent/15 px-1.5 py-0.5 text-[9px] font-semibold text-accent"
                  >
                    <Star className="h-2.5 w-2.5 fill-current" />
                    Keystone
                  </span>
                )}
              </div>
              {characters && <p className="truncate text-[10px] text-zinc-500">{characters}</p>}
            </div>
          </>
        ) : (
          <p className="text-[10px] text-zinc-600">Chain broken — no shared cast</p>
        )}
      </div>
    </div>
  );
}

