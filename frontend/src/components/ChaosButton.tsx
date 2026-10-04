import { Loader2 } from "lucide-react";
import { ApiError } from "../lib/api";
import { useChaos } from "../lib/queries";

/** 🎲 Chaos Button: rolls one random handicap for the next film only. */
export default function ChaosButton({ runId, active }: { runId: string; active: boolean }) {
  const { roll } = useChaos(runId);
  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        disabled={active || roll.isPending}
        onClick={() => roll.mutate()}
        title={active ? "A chaos handicap is already active" : "Roll a one-step handicap for the next film"}
        className="flex items-center gap-1.5 rounded-md border border-yellow-400/50 bg-yellow-400/10 px-2.5 py-2 text-sm font-semibold text-yellow-200 transition-colors hover:bg-yellow-400/20 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {roll.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <span aria-hidden>🎲</span>}
        Chaos Button
      </button>
      {roll.isError && (
        <span role="alert" className="text-[11px] text-amber-400">
          {roll.error instanceof ApiError ? roll.error.message : "Could not roll."}
        </span>
      )}
    </div>
  );
}
