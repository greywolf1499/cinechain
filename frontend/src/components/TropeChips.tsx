import { cn } from "../lib/cn";
import { sharedTropeOf, tropeLabel } from "../lib/tropes";
import type { TropeEvidence } from "../types/api";

/** Small read-only trope pills ("🏷️ heist"); tropes the frontier also has are highlighted. */
export function TropeChips({
  tropes,
  evidence,
  highlight,
  max,
  className,
}: {
  tropes: string[] | null | undefined;
  evidence?: TropeEvidence[];
  highlight?: readonly string[];
  max?: number;
  className?: string;
}) {
  const items = evidence?.length
    ? evidence
    : (tropes ?? []).map((slug) => ({ slug, sources: [], tvtropes_url: null }));
  if (items.length === 0) return null;
  const shown = max === undefined ? items : items.slice(0, max);
  return (
    <div className={cn("flex flex-wrap gap-1", className)} aria-label="Tropes">
      {shown.map((trope) => (
        <span
          key={trope.slug}
          title={`Trope: ${tropeLabel(trope.slug)}`}
          className={cn(
            "rounded-full px-1.5 py-0.5 text-[9px] font-medium",
            highlight?.includes(trope.slug)
              ? "bg-rose-500/20 text-rose-200 ring-1 ring-rose-400/50"
              : "bg-app-surface-hover text-zinc-400",
          )}
        >
          🏷️ {tropeLabel(trope.slug)}
          {trope.sources.includes("tvtropes") && trope.tvtropes_url && (
            <a
              href={trope.tvtropes_url}
              target="_blank"
              rel="noreferrer"
              className="ml-1 underline"
              onClick={(event) => event.stopPropagation()}
            >
              via TV Tropes
            </a>
          )}
          {trope.sources.includes("tvtropes") && !trope.tvtropes_url && (
            <span className="ml-1">via TV Tropes</span>
          )}
          {trope.sources.includes("llm") && <span className="ml-1">AI</span>}
          {trope.sources.includes("manual") && <span className="ml-1">You</span>}
        </span>
      ))}
    </div>
  );
}

/** The prominent "[ 🏷️ Trope: Heist ]" badge for a hop linked by a shared trope. */
export function TropeLinkBadge({
  meta,
  className,
}: {
  meta: Record<string, unknown> | null | undefined;
  className?: string;
}) {
  const trope = sharedTropeOf(meta);
  if (!trope) return null;
  return (
    <span
      title={`Both films share the trope "${trope}"`}
      className={cn(
        "inline-flex w-fit items-center rounded-full bg-gradient-to-r from-rose-500/25 to-orange-400/20 px-2.5 py-0.5 text-[10px] font-semibold text-rose-100 shadow-[0_0_10px_rgba(244,63,94,0.35)] ring-1 ring-rose-400/60",
        className,
      )}
    >
      [ 🏷️ Trope: {tropeLabel(trope)} ]
    </span>
  );
}
