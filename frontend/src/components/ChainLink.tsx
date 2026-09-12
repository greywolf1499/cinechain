import { User } from "lucide-react";
import { profileUrl } from "../lib/tmdbImage";
import type { RunStep } from "../types/api";

interface TransitionMeta {
  actor_id?: number;
  actor_name?: string;
  profile_path?: string | null;
}

export default function ChainLink({ step }: { step: RunStep }) {
  const meta = step.transition_metadata as TransitionMeta | null;
  const actorName = meta?.actor_name;
  const photo = meta?.profile_path ? profileUrl(meta.profile_path) : null;

  return (
    <div className="flex w-16 shrink-0 flex-col items-center justify-center gap-1 self-center text-center">
      <div className="h-px w-full bg-app-border" />
      {actorName ? (
        <>
          {photo ? (
            <img src={photo} alt={actorName} className="h-8 w-8 rounded-full object-cover" />
          ) : (
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500">
              <User className="h-4 w-4" />
            </div>
          )}
          <p className="line-clamp-2 text-[10px] leading-tight text-zinc-400">{actorName}</p>
        </>
      ) : (
        <p className="text-[10px] text-zinc-600">no link</p>
      )}
      <div className="h-px w-full bg-app-border" />
    </div>
  );
}
