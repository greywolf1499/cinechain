import { progressLabel, type MarathonProgress } from "../lib/auteurTrack";

/** `X / Y Films Watched · Z% Complete` over a filled bar. */
export default function MarathonProgressBar({
  progress,
  barClassName = "bg-emerald-400",
}: {
  progress: MarathonProgress;
  barClassName?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs font-medium text-zinc-300">{progressLabel(progress)}</p>
      <div
        role="progressbar"
        aria-label="Marathon progress"
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={progress.watched}
        className="h-2 overflow-hidden rounded-full bg-app-surface-hover"
      >
        <div
          className={`h-full rounded-full transition-all ${barClassName}`}
          style={{ width: `${progress.percent}%` }}
        />
      </div>
    </div>
  );
}
