/** Tomato Meter Divergence: both scores on one 0-100 track with the gap between them filled in. */
export default function SplitMeter({
  critic,
  audience,
  divergence,
}: {
  critic: number;
  audience: number;
  divergence: number;
}) {
  const low = Math.min(critic, audience);
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs font-medium text-zinc-200">
        🍅 Critics: {critic}% <span className="text-zinc-600">|</span> 🍿 Audience: {audience}%{" "}
        <span className="text-amber-300">(Δ {divergence}%)</span>
      </p>
      <div
        role="img"
        aria-label={`Critics ${critic} percent, audience ${audience} percent, a gap of ${divergence}`}
        className="relative h-2 rounded-full bg-app-surface-hover"
      >
        <div
          className="absolute inset-y-0 rounded-full bg-gradient-to-r from-red-500/70 to-amber-400/70"
          style={{
            left: `${low}%`,
            width: `${divergence}%`,
            ...(critic > audience ? { backgroundImage: "linear-gradient(to left, rgba(239,68,68,.7), rgba(251,191,36,.7))" } : {}),
          }}
        />
        <span
          className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 text-xs leading-none"
          style={{ left: `${critic}%` }}
        >
          🍅
        </span>
        <span
          className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 text-xs leading-none"
          style={{ left: `${audience}%` }}
        >
          🍿
        </span>
      </div>
    </div>
  );
}
