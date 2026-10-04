import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { needsChaser } from "../lib/chaser";
import { useMovieDetail } from "../lib/queries";
import type { RunStep } from "../types/api";

/** 🍺 Grab a Chaser: after a heavy film (135+ min or a drama) offer a short, light palate cleanser.
 * An inline action button stays while the heavy film is the frontier; a toast pops up when the
 * film is logged during this visit. */
export default function ChaserPrompt({
  frontier,
  onGrab,
}: {
  frontier: RunStep;
  onGrab: () => void;
}) {
  const { movie } = useMovieDetail(frontier.movie_id);
  const heavy = !!movie && needsChaser(movie.runtime, movie.genre_ids);
  const initialStepId = useRef(frontier.id);
  const [dismissedStepId, setDismissedStepId] = useState<string | null>(null);
  const loggedThisVisit = frontier.id !== initialStepId.current;
  const showToast = heavy && loggedThisVisit && dismissedStepId !== frontier.id;

  // Let the toast fade away on its own after a while.
  useEffect(() => {
    if (!showToast) return;
    const timer = window.setTimeout(() => setDismissedStepId(frontier.id), 12000);
    return () => window.clearTimeout(timer);
  }, [showToast, frontier.id]);

  if (!heavy) return null;
  return (
    <>
      <button
        type="button"
        onClick={onGrab}
        className="flex items-center justify-center gap-1.5 rounded-md border border-amber-400/50 bg-amber-400/10 px-3.5 py-2.5 text-sm font-semibold text-amber-200 transition-colors hover:bg-amber-400/20"
      >
        <span aria-hidden>🍺</span>
        Grab a Chaser (Palate Cleanser)
      </button>
      {showToast && (
        <div
          role="status"
          className="animate-bounty-slide-in fixed bottom-5 right-5 z-40 flex max-w-xs items-start gap-3 rounded-xl border border-amber-400/50 bg-app-surface p-3.5 shadow-2xl shadow-black/60"
        >
          <span className="animate-bounce text-2xl" aria-hidden>
            🍺
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <p className="text-sm text-zinc-200">
              <span className="font-semibold">{frontier.movie_title}</span> was a heavy one. Cleanse the palate?
            </p>
            <button
              type="button"
              onClick={() => {
                setDismissedStepId(frontier.id);
                onGrab();
              }}
              className="w-fit rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-accent-strong"
            >
              🍺 Grab a Chaser (Palate Cleanser)
            </button>
          </div>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setDismissedStepId(frontier.id)}
            className="rounded p-1 text-zinc-500 hover:text-zinc-200"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </>
  );
}
