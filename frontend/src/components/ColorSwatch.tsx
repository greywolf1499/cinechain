import { cn } from "../lib/cn";

/** A small circle filled with a poster's dominant colour (Aesthetic Gradient). */
export default function ColorSwatch({
  color,
  className,
}: {
  color: string | null | undefined;
  className?: string;
}) {
  if (!color) return null;
  return (
    <span
      role="img"
      aria-label={`Dominant colour ${color}`}
      title={`Dominant colour ${color}`}
      style={{ backgroundColor: color }}
      className={cn("inline-block h-5 w-5 shrink-0 rounded-full border-2 border-white/70 shadow", className)}
    />
  );
}

/** "Semantic Match: 89%" - plot similarity between two films (Semantic Trope Web). */
export function SemanticMatchBadge({
  score,
  className,
}: {
  score: number | null | undefined;
  className?: string;
}) {
  if (score === null || score === undefined) return null;
  const percent = Math.round(Math.max(0, score) * 100);
  return (
    <span
      title="How closely this film's plot matches the previous film's, by on-device text embeddings"
      className={cn(
        "w-fit rounded-full px-2 py-0.5 text-[9px] font-semibold",
        percent >= 75 ? "bg-emerald-950 text-emerald-300" : "bg-sky-950 text-sky-300",
        className,
      )}
    >
      Semantic Match: {percent}%
    </span>
  );
}
