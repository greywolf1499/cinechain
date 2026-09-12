import { useState } from "react";
import { Loader2 } from "lucide-react";
import Modal from "./Modal";
import { useMarkStepWatched } from "../lib/queries";

export default function MarkWatchedModal({
  open,
  onClose,
  runId,
  stepId,
  movieTitle,
}: {
  open: boolean;
  onClose: () => void;
  runId: string;
  stepId: string;
  movieTitle: string;
}) {
  const [watchedDate, setWatchedDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState("");
  const markWatched = useMarkStepWatched(runId);

  async function handleSubmit() {
    await markWatched.mutateAsync({
      stepId,
      watched_at: new Date(watchedDate).toISOString(),
      user_notes: notes.trim() || null,
    });
    onClose();
  }

  return (
    <Modal open={open} onClose={onClose} title={`Mark "${movieTitle}" as watched`} widthClassName="max-w-sm">
      <div className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
          Watched on
          <input
            type="date"
            value={watchedDate}
            max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setWatchedDate(e.target.value)}
            className="rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 focus:border-accent focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
          Notes (optional)
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="Thoughts, memories..."
            className="resize-none rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none"
          />
        </label>
        <button
          type="button"
          disabled={markWatched.isPending}
          onClick={handleSubmit}
          className="flex items-center justify-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
        >
          {markWatched.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Mark as watched
        </button>
      </div>
    </Modal>
  );
}
