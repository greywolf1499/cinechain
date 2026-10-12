import { useId, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import type { PitchResult } from "../types/api";
import Popover from "./ui/Popover";

type PitchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; result: PitchResult }
  | { status: "error"; message: string };

interface PitchProps {
  previousMovieId: number;
  candidateMovieId: number;
  runId?: string;
  contextKey?: string;
  linkLabel?: string | null;
  className?: string;
}

export default function PitchButton(props: PitchProps) {
  return <PitchForPair key={`${props.runId}:${props.previousMovieId}:${props.candidateMovieId}:${props.contextKey}`} {...props} />;
}

function PitchForPair({
  previousMovieId,
  candidateMovieId,
  linkLabel,
  className,
  runId,
}: PitchProps) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<PitchState>({ status: "idle" });
  const popoverId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || state.status === "loading" || state.status === "ready") return;
    setState({ status: "loading" });
    try {
      const result = await api.post<PitchResult>(runId ? `/runs/${runId}/pitch` : "/engine/pitch", {
        previous_movie_id: previousMovieId,
        candidate_movie_id: candidateMovieId,
        link_label: linkLabel || null,
      });
      if (result.candidate_movie_id !== candidateMovieId) {
        throw new Error("The explanation belongs to a different film. Please try again.");
      }
      setState({ status: "ready", result });
    } catch (err) {
      setState({
        status: "error",
        message: err instanceof Error ? err.message : "Couldn't load this explanation.",
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
        title="Explain this connection in the active game mode"
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
        className="w-72 max-w-[calc(100vw-2rem)] border-fuchsia-400/30 p-3 text-left text-xs leading-relaxed text-zinc-200"
      >
        {state.status === "loading" && (
          <span className="flex items-center gap-2 text-zinc-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Checking the connection...
          </span>
        )}
        {state.status === "ready" && <div className="flex flex-col gap-2">
          <p>{state.result.bridge ?? state.result.pitch}</p>
          {state.result.candidate_hook && <p>{state.result.candidate_hook}</p>}
          <span className="text-[10px] text-zinc-500">{state.result.source === "ai" ? "AI · grounded in film facts" : "Film facts · no AI required"}</span>
        </div>}
        {state.status === "error" && <p className="text-amber-300">{state.message}</p>}
      </Popover>
    </span>
  );
}
