import { cn } from "../lib/cn";

const thumb =
  "[&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 " +
  "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full " +
  "[&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-zinc-950 [&::-webkit-slider-thumb]:bg-accent " +
  "[&::-moz-range-thumb]:pointer-events-auto [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 " +
  "[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-zinc-950 " +
  "[&::-moz-range-thumb]:bg-accent";

/** Two thumbs on one track: `[low, high]` can never cross. */
export default function DualRangeSlider({
  min,
  max,
  step,
  value,
  onChange,
  label,
}: {
  min: number;
  max: number;
  step: number;
  value: [number, number];
  onChange: (value: [number, number]) => void;
  label: string;
}) {
  const [low, high] = value;
  const span = max - min;
  const left = ((low - min) / span) * 100;
  const right = ((high - min) / span) * 100;

  return (
    <div className="relative h-5">
      <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-app-surface-hover" />
      <div
        className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-accent"
        style={{ left: `${left}%`, right: `${100 - right}%` }}
      />
      <input
        type="range"
        aria-label={`${label} minimum`}
        min={min}
        max={max}
        step={step}
        value={low}
        onChange={(e) => onChange([Math.min(Number(e.target.value), high), high])}
        className={cn(
          "pointer-events-none absolute inset-0 h-5 w-full appearance-none bg-transparent",
          // The lower thumb must stay reachable when both thumbs sit at the top end.
          low >= max - step && "z-10",
          thumb,
        )}
      />
      <input
        type="range"
        aria-label={`${label} maximum`}
        min={min}
        max={max}
        step={step}
        value={high}
        onChange={(e) => onChange([low, Math.max(Number(e.target.value), low)])}
        className={cn("pointer-events-none absolute inset-0 h-5 w-full appearance-none bg-transparent", thumb)}
      />
    </div>
  );
}
