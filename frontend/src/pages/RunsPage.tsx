import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Film, Loader2, Plus } from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";
import StatusBadge from "../components/StatusBadge";
import Modal from "../components/Modal";
import MovieSearchAutocomplete from "../components/MovieSearchAutocomplete";
import { api } from "../lib/api";
import { useCreateRun, useRuns, useUsers } from "../lib/queries";
import type { EngineMeta, MovieSummary } from "../types/api";

export default function RunsPage() {
  const navigate = useNavigate();
  const { data: runs, isLoading } = useRuns();
  const [showNewRunModal, setShowNewRunModal] = useState(false);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <PageHeading title="Runs" subtitle="Your active and past movie challenges" />
        <button
          type="button"
          onClick={() => setShowNewRunModal(true)}
          className="flex h-9 items-center gap-1.5 rounded-md bg-accent px-3.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong"
        >
          <Plus className="h-4 w-4" />
          New Run
        </button>
      </div>

      {isLoading && (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-zinc-600" />
        </div>
      )}

      {!isLoading && runs?.length === 0 && (
        <EmptyState
          icon={Film}
          title="No runs yet"
          description="Start a run to begin chaining films by shared cast."
        />
      )}

      {!isLoading && runs && runs.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {runs.map((run) => (
            <button
              key={run.id}
              type="button"
              onClick={() => navigate(`/runs/${run.id}`)}
              className="rounded-xl border border-app-border bg-app-surface p-4 text-left transition-colors hover:border-accent/50"
            >
              <div className="mb-2 flex items-center justify-between">
                <h3 className="truncate text-sm font-semibold text-zinc-100">{run.name}</h3>
                <StatusBadge status={run.status} />
              </div>
              <p className="text-xs text-zinc-500">
                Started {new Date(run.created_at).toLocaleDateString()}
              </p>
            </button>
          ))}
        </div>
      )}

      <NewRunModal
        open={showNewRunModal}
        onClose={() => setShowNewRunModal(false)}
        onCreated={(runId) => navigate(`/runs/${runId}`)}
      />
    </div>
  );
}

function NewRunModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (runId: string) => void;
}) {
  const { data: users } = useUsers();
  const { data: engines } = useQuery({
    queryKey: ["engines"],
    queryFn: () => api.get<EngineMeta[]>("/engines"),
  });
  const createRun = useCreateRun();

  const [name, setName] = useState("");
  const [gameType, setGameType] = useState("cinechain");
  const [participantIds, setParticipantIds] = useState<string[]>([]);
  const [seedMovie, setSeedMovie] = useState<MovieSummary | null>(null);

  function toggleParticipant(userId: string) {
    setParticipantIds((prev) =>
      prev.includes(userId) ? prev.filter((id) => id !== userId) : [...prev, userId],
    );
  }

  function reset() {
    setName("");
    setGameType("cinechain");
    setParticipantIds([]);
    setSeedMovie(null);
  }

  async function handleSubmit() {
    if (!name.trim()) return;
    const run = await createRun.mutateAsync({
      name: name.trim(),
      game_type: gameType,
      participant_user_ids: participantIds,
      seed_movie_id: seedMovie?.tmdb_id ?? null,
    });
    reset();
    onClose();
    onCreated(run.id);
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Start a new run"
    >
      <div className="flex flex-col gap-4">
        <Field label="Run name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Bacon Sunday"
            className={inputClass}
          />
        </Field>

        <Field label="Game type">
          <select
            value={gameType}
            onChange={(e) => setGameType(e.target.value)}
            className={inputClass}
          >
            {(engines ?? [{ game_type: "cinechain", display_name: "CineChain" }]).map((engine) => (
              <option key={engine.game_type} value={engine.game_type}>
                {engine.display_name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Participants">
          <div className="flex flex-col gap-1.5 rounded-md border border-app-border bg-app-bg p-2">
            {users?.map((user) => (
              <label
                key={user.id}
                className="flex items-center gap-2 rounded px-1.5 py-1 text-sm text-zinc-300 hover:bg-app-surface-hover"
              >
                <input
                  type="checkbox"
                  checked={participantIds.includes(user.id)}
                  onChange={() => toggleParticipant(user.id)}
                  className="accent-accent"
                />
                {user.display_name}
              </label>
            ))}
          </div>
        </Field>

        <Field label="Seed movie (optional)">
          <MovieSearchAutocomplete onSelect={setSeedMovie} placeholder="Search for a starting film..." />
        </Field>

        <button
          type="button"
          disabled={!name.trim() || createRun.isPending}
          onClick={handleSubmit}
          className="mt-1 flex items-center justify-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
        >
          {createRun.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Create run
        </button>
      </div>
    </Modal>
  );
}

const inputClass =
  "w-full rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
      {label}
      {children}
    </label>
  );
}
