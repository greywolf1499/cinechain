import { cn } from "../lib/cn";
import { characterHopOf, goldenReunionOf } from "../lib/linkBonuses";

/** The glowing "Golden Reunion" and "Character Hop" badges for a hop's metadata. */
export default function LinkBonusBadges({
  meta,
  className,
}: {
  meta: Record<string, unknown> | null | undefined;
  className?: string;
}) {
  const reunion = goldenReunionOf(meta);
  const hop = characterHopOf(meta);
  if (!reunion && !hop) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {reunion && (
        <span
          title={`${reunion.director} directed again, and ${reunion.actor} is back from the previous film`}
          className="inline-flex items-center rounded-full bg-gradient-to-r from-yellow-300/25 via-amber-300/20 to-yellow-300/25 px-2 py-0.5 text-[10px] font-semibold text-yellow-200 shadow-[0_0_12px_rgba(250,204,21,0.45)] ring-1 ring-yellow-300/60"
        >
          🌟 Golden Reunion: Director &amp; Actor
        </span>
      )}
      {hop && (
        <span
          title={`The same character, played by a different actor: ${hop}`}
          className="inline-flex items-center rounded-full bg-fuchsia-500/15 px-2 py-0.5 text-[10px] font-semibold text-fuchsia-200 ring-1 ring-fuchsia-400/40"
        >
          🎭 Character Hop: {hop}
        </span>
      )}
    </div>
  );
}
