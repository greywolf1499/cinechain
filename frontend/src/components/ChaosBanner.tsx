import { useChaos } from "../lib/queries";
import { chaosParts } from "../lib/chaos";
import type { ActiveChaos } from "../types/api";

/** The pulsating hazard banner shown while a Chaos handicap is live. */
export default function ChaosBanner({
  runId,
  chaos,
  className = "",
}: {
  runId: string;
  chaos: ActiveChaos;
  className?: string;
}) {
  const { cancel } = useChaos(runId);
  const { title, rule } = chaosParts(chaos);
  return (
    <div
      role="alert"
      className={`animate-pulse rounded-lg border-2 border-yellow-400/70 px-3 py-2.5 ${className}`}
      style={{
        backgroundImage:
          "repeating-linear-gradient(45deg, rgba(250,204,21,0.14) 0 14px, rgba(15,17,23,0.85) 14px 28px)",
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-bold text-yellow-200">
          🎲 Chaos Active: {rule}!{" "}
          <span className="font-medium text-yellow-300/80">(This step only)</span>
        </p>
        <div className="flex items-center gap-2">
          {title && <span className="text-[11px] font-semibold uppercase tracking-wide text-yellow-300/70">{title}</span>}
          <button
            type="button"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
            className="rounded-md border border-yellow-400/40 bg-app-bg/80 px-2 py-1 text-[11px] font-medium text-yellow-200 hover:bg-app-surface-hover disabled:opacity-50"
          >
            Cancel chaos
          </button>
        </div>
      </div>
    </div>
  );
}
