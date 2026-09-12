import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Clapperboard, GitBranch, Loader2, Plus, Trash2 } from "lucide-react";
import ChainTimeline from "../components/ChainTimeline";
import ForkInTheRoadModal from "../components/ForkInTheRoadModal";
import Modal from "../components/Modal";
import MovieSearchAutocomplete from "../components/MovieSearchAutocomplete";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import { useDeleteStep, useRun, useUsers } from "../lib/queries";
import type { ActorClickPayload } from "../components/actorClickTypes";

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(id);
  const { data: users } = useUsers();
  const deleteStep = useDeleteStep(id ?? "");

  const [showPicker, setShowPicker] = useState(false);
  const [activeActor, setActiveActor] = useState<ActorClickPayload | null>(null);
  const [confirmDeleteStepId, setConfirmDeleteStepId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-zinc-600" />
      </div>
    );
  }

  if (!run) {
    return (
      <EmptyState
        icon={Clapperboard}
        title="Run not found"
        description="This run doesn't exist, or you're not a participant."
      />
    );
  }

  const lastStep = run.steps[run.steps.length - 1];
  const participantNames = run.participants
    .map((p) => users?.find((u) => u.id === p.user_id)?.display_name ?? p.user_id)
    .join(", ");

  async function handleDeleteStep() {
    if (!confirmDeleteStepId) return;
    await deleteStep.mutateAsync(confirmDeleteStepId);
    setConfirmDeleteStepId(null);
  }

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold tracking-tight text-zinc-100">{run.name}</h1>
            <StatusBadge status={run.status} />
          </div>
          <p className="mt-1 text-sm text-zinc-500">{participantNames || "No participants"}</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setShowPicker((v) => !v)}
            className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong"
          >
            <Plus className="h-4 w-4" />
            Pick next movie
          </button>
          <button
            type="button"
            onClick={() => navigate("/bridge")}
            className="flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
          >
            <GitBranch className="h-4 w-4" />
            Bridge Solver
          </button>
        </div>
      </div>

      {showPicker && (
        <div className="mb-6">
          <MovieSearchAutocomplete
            runId={run.id}
            tailMovieId={lastStep?.movie_id}
            onLogged={() => setShowPicker(false)}
          />
        </div>
      )}

      {run.steps.length === 0 ? (
        <EmptyState
          icon={Clapperboard}
          title="No films logged yet"
          description="Pick the first movie to kick off this run."
        />
      ) : (
        <>
          <ChainTimeline steps={run.steps} onActorClick={setActiveActor} />
          {lastStep && (
            <div className="mt-2 flex justify-end">
              <button
                type="button"
                onClick={() => setConfirmDeleteStepId(lastStep.id)}
                className="flex items-center gap-1.5 text-xs text-zinc-500 transition-colors hover:text-red-400"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Remove last step
              </button>
            </div>
          )}
        </>
      )}

      {activeActor && (
        <ForkInTheRoadModal
          open={!!activeActor}
          onClose={() => setActiveActor(null)}
          actorId={activeActor.actorId}
          actorName={activeActor.actorName}
          actorProfilePath={activeActor.profilePath}
          runId={run.id}
        />
      )}

      <Modal
        open={!!confirmDeleteStepId}
        onClose={() => setConfirmDeleteStepId(null)}
        title="Remove this step?"
        widthClassName="max-w-sm"
      >
        <p className="text-sm text-zinc-400">
          This removes the most recently logged film from the chain. This can't be undone.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setConfirmDeleteStepId(null)}
            className="rounded-md px-3 py-1.5 text-sm text-zinc-400 hover:bg-app-surface-hover"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={deleteStep.isPending}
            onClick={handleDeleteStep}
            className="flex items-center gap-1.5 rounded-md bg-red-950 px-3 py-1.5 text-sm font-medium text-red-300 transition-colors hover:bg-red-900 disabled:opacity-60"
          >
            {deleteStep.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Remove
          </button>
        </div>
      </Modal>
    </div>
  );
}
