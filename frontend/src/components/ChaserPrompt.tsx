import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { useNeedsChaser } from "../lib/chaser";
import type { RulesConfig, RunStep } from "../types/api";

/** 🍺 Grab a Chaser: use the catalogue's heavy-film rule to offer a light palate cleanser.
 * An inline action button stays while the heavy film is the frontier; a toast pops up when the
 * film is logged during this visit. */
export default function ChaserPrompt({
  frontier,
  rules,
  onGrab,
}: {
  frontier: RunStep;
  rules: RulesConfig;
  onGrab: () => void;
}) {
  const { heavy, error } = useNeedsChaser(rules);
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

  if (error) return <p role="alert" className="text-xs text-amber-300">Could not load Chaser rules.</p>;
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
              Recent films have run above your comfort level. Pick something lighter?
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
