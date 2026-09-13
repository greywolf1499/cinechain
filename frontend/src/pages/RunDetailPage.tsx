import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Clapperboard, GitBranch, Loader2, Plus } from "lucide-react";
import ChainTimeline from "../components/ChainTimeline";
import ForkInTheRoadModal from "../components/ForkInTheRoadModal";
import Modal from "../components/Modal";
import MoviePoster from "../components/MoviePoster";
import MovieSearchAutocomplete from "../components/MovieSearchAutocomplete";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import { isoToFlagEmoji } from "../lib/countries";
import { useDeleteStep, useRun, useRunStats, useUsers } from "../lib/queries";
import type { ActorClickPayload } from "../components/actorClickTypes";
import type { RulesConfig, RunStats, RunStep } from "../types/api";

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: run, isLoading } = useRun(id);
  const { data: users } = useUsers();
  const { data: stats } = useRunStats(id);
  const deleteStep = useDeleteStep(id ?? "");

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
  // "Keystone" = an actor who has actually bridged more than one hop, not just
  // any connector (compute_stats includes every connector at least once).
  const keystoneActorIds = new Set(
    (stats?.keystone_actors ?? []).filter((a) => a.appearances >= 2).map((a) => a.actor_id),
  );

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
      </div>

      {run.steps.length === 0 ? (
        <EmptyState
          icon={Clapperboard}
          title="No films logged yet"
          description="Pick the first movie to kick off this run."
        />
      ) : (
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-12">
          {/* Right rail: renders first (top) on mobile via source order; pinned
              to the right column on desktop via explicit grid placement. */}
          <div className="order-1 flex flex-col gap-5 lg:order-2 lg:col-span-5 lg:col-start-8 lg:sticky lg:top-20 lg:self-start xl:col-span-4 xl:col-start-9">
            <ActiveFrontierCard runId={run.id} tailStep={lastStep} />
            <MiniPassportWidget stats={stats} rules={run.rules_config} />
            <RulesSummaryCard rules={run.rules_config} />
          </div>

          <div className="order-2 min-w-0 lg:order-1 lg:col-span-7 lg:col-start-1 xl:col-span-8">
            <ChainTimeline
              runId={run.id}
              steps={run.steps}
              keystoneActorIds={keystoneActorIds}
              onActorClick={setActiveActor}
              onRequestDeleteStep={setConfirmDeleteStepId}
            />
          </div>
        </div>
      )}

      {activeActor && (
        <ForkInTheRoadModal
          open={!!activeActor}
          onClose={() => setActiveActor(null)}
          actorId={activeActor.actorId}
          actorName={activeActor.actorName}
          actorProfilePath={activeActor.profilePath}
          actorCharacterName={activeActor.characterName}
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

function ActiveFrontierCard({ runId, tailStep }: { runId: string; tailStep: RunStep | undefined }) {
  const navigate = useNavigate();
  const [showPicker, setShowPicker] = useState(false);

  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">
        Active Frontier
      </p>

      {tailStep ? (
        <div className="mb-4 flex items-center gap-3">
          <MoviePoster path={tailStep.movie_poster_path} title={tailStep.movie_title} className="w-14" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-zinc-100">{tailStep.movie_title}</p>
            <p className="text-xs text-zinc-500">{tailStep.movie_release_year ?? "—"}</p>
          </div>
        </div>
      ) : (
        <p className="mb-4 text-sm text-zinc-500">No films logged yet.</p>
      )}

      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => setShowPicker((v) => !v)}
          className="flex items-center justify-center gap-1.5 rounded-md bg-accent px-3.5 py-2.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong"
        >
          <Plus className="h-4 w-4" />
          Pick Next Movie
        </button>
        <button
          type="button"
          onClick={() => navigate("/bridge")}
          className="flex items-center justify-center gap-1.5 rounded-md border border-app-border px-3.5 py-2.5 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
        >
          <GitBranch className="h-4 w-4" />
          Bridge Solver
        </button>
      </div>

      {showPicker && (
        <div className="mt-4">
          <MovieSearchAutocomplete
            runId={runId}
            tailMovieId={tailStep?.movie_id}
            onLogged={() => setShowPicker(false)}
          />
        </div>
      )}
    </div>
  );
}

function MiniPassportWidget({ stats, rules }: { stats: RunStats | undefined; rules: RulesConfig }) {
  const topActor = stats?.keystone_actors[0];

  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">Passport</p>
      {!stats ? (
        <div className="flex justify-center py-4">
          <Loader2 className="h-4 w-4 animate-spin text-zinc-600" />
        </div>
      ) : (
        <div className="flex flex-col gap-2.5 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="text-zinc-400">Countries</span>
            <div className="flex flex-wrap justify-end gap-1">
              {stats.countries.length === 0 ? (
                <span className="text-zinc-600">—</span>
              ) : (
                stats.countries.map((country) => (
                  <span key={country} title={country}>
                    {isoToFlagEmoji(country)}
                  </span>
                ))
              )}
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Decades</span>
            <span className="text-zinc-200">{stats.decades.length}</span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="shrink-0 text-zinc-400">Keystone Actor</span>
            <span className="max-w-[65%] truncate rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
              {topActor ? topActor.actor_name : "—"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Wildcards Left</span>
            <span className="text-zinc-200">
              {rules.wildcards_budget === -1 ? "Unlimited" : rules.wildcards_budget}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function RulesSummaryCard({ rules }: { rules: RulesConfig }) {
  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">Ruleset</p>
      <div className="flex flex-col gap-2.5 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-zinc-400">Preset</span>
          <span className="capitalize text-zinc-200">{rules.preset}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-zinc-400">Cast Depth</span>
          <span className="text-zinc-200">Top {rules.max_cast_order}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-zinc-400">Repeats</span>
          <span className="capitalize text-zinc-200">{rules.allow_repeats}</span>
        </div>
      </div>
    </div>
  );
}
