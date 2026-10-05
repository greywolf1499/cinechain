import { useId, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { ApiError, api } from "../lib/api";
import { cn } from "../lib/cn";
import { useLlmStatus } from "../lib/queries";
import type { PitchResult } from "../types/api";
import Popover from "./ui/Popover";

type PitchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; text: string }
  | { status: "error"; message: string };

/** "✨ Why this link?": asks the local LLM for a one-sentence cinephile pitch for the hop
 * between two films and shows it in a popover. Renders nothing while the model is off. */
export default function PitchButton({
  previousMovieId,
  candidateMovieId,
  linkLabel,
  className,
}: {
  previousMovieId: number;
  candidateMovieId: number;
  /** What links the two films (an actor's name), to ground the pitch. */
  linkLabel?: string | null;
  className?: string;
}) {
  const { data: status } = useLlmStatus();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<PitchState>({ status: "idle" });
  const popoverId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);

  if (!status?.enabled) return null;

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || state.status === "loading" || state.status === "ready") return;
    setState({ status: "loading" });
    try {
      const result = await api.post<PitchResult>("/engine/pitch", {
        previous_movie_id: previousMovieId,
        candidate_movie_id: candidateMovieId,
        link_label: linkLabel || null,
      });
      setState({ status: "ready", text: result.pitch });
    } catch (err) {
      setState({
        status: "error",
        message: err instanceof ApiError ? err.message : "Couldn't reach the model.",
      });
    }
  }

  return (
    <span className={cn("inline-block", className)}>
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? popoverId : undefined}
        title="Ask the AI why these two films belong together"
        className="flex items-center gap-1 rounded-full border border-fuchsia-400/30 bg-fuchsia-500/10 px-2 py-0.5 text-[10px] font-medium text-fuchsia-300 transition-colors hover:bg-fuchsia-500/20"
      >
        <span aria-hidden>✨</span> Why this link?
      </button>
      <Popover
        anchorRef={buttonRef}
        open={open}
        onClose={() => setOpen(false)}
        label="Why these films belong together"
        placement="top"
        matchAnchorWidth={false}
        panelId={popoverId}
        className="w-60 border-fuchsia-400/30 p-3 text-left text-xs leading-relaxed text-zinc-200"
      >
        {state.status === "loading" && (
          <span className="flex items-center gap-2 text-zinc-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Thinking up a pitch...
          </span>
        )}
        {state.status === "ready" && <p className="italic">&ldquo;{state.text}&rdquo;</p>}
        {state.status === "error" && <p className="text-amber-300">{state.message}</p>}
      </Popover>
    </span>
  );
}
