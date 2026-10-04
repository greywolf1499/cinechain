import { ArrowDown, ArrowUp, Clapperboard, Star, User } from "lucide-react";
import { cn } from "../lib/cn";
import { countryName } from "../lib/countryNames";
import { isoToFlagEmoji } from "../lib/countries";
import { profileUrl } from "../lib/tmdbImage";
import { ROLE_STYLES, stepCraftLink } from "../lib/crewRoles";
import { SemanticMatchBadge } from "./ColorSwatch";
import LinkBonusBadges from "./LinkBonusBadges";
import RoleBadge from "./RoleBadge";
import PitchButton from "./PitchButton";
import type { RunStep } from "../types/api";

interface TransitionMeta {
  connection_type?: "actor" | "director";
  director_id?: number;
  director_name?: string;
  actor_id?: number;
  actor_name?: string;
  person_id?: number;
  person_name?: string;
  role?: string;
  from_role?: string;
  profile_path?: string | null;
  character_in_from?: string | null;
  character_in_to?: string | null;
  character_hop?: string;
  pendulum_genre?: string;
  pendulum_step?: number;
  semantic_score?: number;
  color_distance?: number;
  year_delta?: number;
  from_country?: string | null;
  to_country?: string | null;
}

/** Renders on the vertical spine track between two station cards. */
export default function ChainLink({
  step,
  isKeystone,
  castLinked = true,
  previousMovieId,
}: {
  step: RunStep;
  isKeystone: boolean;
  castLinked?: boolean;
  /** The film this step was linked from: enables the "Why this link?" pitch. */
  previousMovieId?: number;
}) {
  const meta = step.transition_metadata as TransitionMeta | null;
  const craft = stepCraftLink(step);
  const isDirector = meta?.connection_type === "director";
  const actorName = craft ? craft.name : isDirector ? meta?.director_name : meta?.actor_name;
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
            {craft && !photo ? (
              <div
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-base",
                  ROLE_STYLES[craft.role].className,
                )}
                aria-hidden
              >
                {ROLE_STYLES[craft.role].emoji}
              </div>
            ) : isDirector ? (
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
                {craft && (
                  <RoleBadge
                    role={craft.role}
                    name={craft.name}
                    fromRole={craft.fromRole}
                    className="shrink"
                  />
                )}
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
              <LinkBonusBadges meta={step.transition_metadata} className="mt-1" />
            </div>
          </>
        ) : meta?.character_hop ? (
          <p className="text-xs font-medium text-fuchsia-300">🎭 Character Hop: {meta.character_hop}</p>
        ) : castLinked ? (
          <p className="text-[10px] text-zinc-600">Chain broken — no shared cast</p>
        ) : (
          <RuleLink meta={meta} />
        )}
        {previousMovieId !== undefined && (
          <PitchButton
            previousMovieId={previousMovieId}
            candidateMovieId={step.movie_id}
            linkLabel={actorName}
            className="ml-auto shrink-0"
          />
        )}
        <SemanticMatchBadge score={meta?.semantic_score} className="ml-auto shrink-0" />
        {meta?.color_distance !== undefined && (
          <span
            title="Euclidean RGB distance between the two posters' dominant colours (lower = smoother)"
            className="ml-auto shrink-0 rounded-full bg-app-surface-hover px-2 py-0.5 text-[9px] font-semibold text-zinc-400"
          >
            Colour Δ {Math.round(meta.color_distance)}
          </span>
        )}
      </div>
    </div>
  );
}


/** The hop's rule evidence for modes with no cast link: year jump, country change, colour/plot match. */
function RuleLink({ meta }: { meta: TransitionMeta | null }) {
  if (meta?.pendulum_genre) {
    return (
      <p className="flex items-center gap-1.5 text-xs font-medium text-red-300">
        🎯 {meta.pendulum_genre}
        <span className="text-[10px] font-normal text-zinc-500">swing step {meta.pendulum_step}</span>
      </p>
    );
  }
  if (meta?.year_delta !== undefined) {
    const up = meta.year_delta > 0;
    const Icon = up ? ArrowUp : ArrowDown;
    return (
      <p className="flex items-center gap-1.5 text-xs font-medium text-violet-300">
        <Icon className="h-3.5 w-3.5" />
        {Math.abs(meta.year_delta)} year{Math.abs(meta.year_delta) === 1 ? "" : "s"} {up ? "later" : "earlier"}
      </p>
    );
  }
  if (meta?.from_country || meta?.to_country) {
    return (
      <p className="flex items-center gap-1.5 text-xs font-medium text-teal-300">
        {flagAndName(meta.from_country)}
        <span className="text-zinc-500">→</span>
        {flagAndName(meta.to_country)}
      </p>
    );
  }
  if (meta?.semantic_score !== undefined || meta?.color_distance !== undefined) {
    return <p className="text-xs text-zinc-500">Linked by the mode's rule</p>;
  }
  return <p className="text-[10px] text-zinc-600">Linked by the mode's rule</p>;
}

function flagAndName(code: string | null | undefined): string {
  return code ? `${isoToFlagEmoji(code)} ${countryName(code, code)}` : "Unknown";
}
