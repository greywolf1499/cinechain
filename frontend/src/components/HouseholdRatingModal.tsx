import { useState } from "react";
import { Loader2 } from "lucide-react";
import Modal from "./Modal";
import SplitMeter from "./SplitMeter";
import { ApiError } from "../lib/api";
import { useCreateStep } from "../lib/queries";
import { SPLIT_TEAMS, isValidHousehold, settlePoint, settlementOf } from "../lib/splitScore";
import type { SplitCandidate } from "../types/api";

/** Log a split film: the household agrees one joint rating (1-100) and the closer side gets the
 * point (the server settles it; a tie goes to the audience). */
export default function HouseholdRatingModal({
  runId,
  film,
  onClose,
}: {
  runId: string;
  film: SplitCandidate | null;
  onClose: () => void;
}) {
  const createStep = useCreateStep(runId);
  const [score, setScore] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [awarded, setAwarded] = useState<string | null>(null);

  const rating = Number(score);
  const valid = score.trim() !== "" && isValidHousehold(rating);
  const winner = film && valid ? settlePoint(rating, film.critic_score, film.audience_score) : null;

  function close() {
    setScore("");
    setError(null);
    setAwarded(null);
    onClose();
  }

  async function submit() {
    if (!film || !valid) return;
    setError(null);
    try {
      const step = await createStep.mutateAsync({
        movie_id: film.movie_id,
        status: "watched",
        watched_at: new Date().toISOString(),
        household_score: rating,
      });
      const point = settlementOf(step)?.point_to ?? winner;
      setAwarded(point ? `Point to ${SPLIT_TEAMS[point].label}!` : "Logged.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not log that film.");
    }
  }

  return (
    <Modal open={film !== null} onClose={close} title="Log Movie">
      {film && (
        <div className="flex flex-col gap-4">
          <div>
            <p className="text-sm font-semibold text-zinc-100">
              {film.title} {film.year && <span className="font-normal text-zinc-500">({film.year})</span>}
            </p>
          </div>
          <SplitMeter critic={film.critic_score} audience={film.audience_score} divergence={film.divergence} />

          {awarded ? (
            <div className="flex flex-col gap-3">
              <p role="status" className="rounded-md border border-emerald-800/60 bg-emerald-950/30 px-3 py-2 text-sm font-medium text-emerald-300">
                {awarded}
              </p>
              <button
                type="button"
                onClick={close}
                className="self-end rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-accent-strong"
              >
                Done
              </button>
            </div>
          ) : (
            <>
              <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
                Household Rating (1-100)
                <input
                  type="number"
                  min={1}
                  max={100}
                  autoFocus
                  value={score}
                  onChange={(e) => setScore(e.target.value)}
                  placeholder="e.g. 70"
                  className="w-full rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </label>
              <p className="min-h-5 text-xs text-zinc-400" aria-live="polite">
                {winner
                  ? `${SPLIT_TEAMS[winner].label} wins the point (closer to ${
                      winner === "team_a" ? `the critics' ${film.critic_score}` : `the audience's ${film.audience_score}`
                    }).`
                  : "Rate it together: whichever score you land closer to takes the point."}
              </p>
              {error && (
                <p role="alert" className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
                  {error}
                </p>
              )}
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={close}
                  className="rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-app-surface-hover"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={!valid || createStep.isPending}
                  onClick={() => void submit()}
                  className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-accent-strong disabled:opacity-50"
                >
                  {createStep.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                  Log Watched
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
