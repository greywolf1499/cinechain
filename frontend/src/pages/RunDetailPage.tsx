import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Clapperboard,
  Flag,
  GitBranch,
  Loader2,
  Lock,
  MoreHorizontal,
  Pencil,
  Plus,
  RotateCcw,
  Skull,
  Trash2,
  Trophy,
} from "lucide-react";
import ChainTimeline from "../components/ChainTimeline";
import EditRulesModal from "../components/EditRulesModal";
import ForkInTheRoadModal from "../components/ForkInTheRoadModal";
import Modal from "../components/Modal";
import MoviePoster from "../components/MoviePoster";
import MovieSearchAutocomplete from "../components/MovieSearchAutocomplete";
import PickNextHub from "../components/PickNextHub";
import RouletteSpinner from "../components/RouletteSpinner";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import { isoToFlagEmoji } from "../lib/countries";
import { cn } from "../lib/cn";
import { allowsMovieRepeats } from "../lib/rules";
import { useActiveRunStore } from "../store/activeRunStore";
import {
  useCuratedLists,
  useDeleteRun,
  useDeleteStep,
  useEngines,
  useRun,
  useRunStats,
  useUpdateRun,
  useUsers,
} from "../lib/queries";
import type { ActorClickPayload } from "../components/actorClickTypes";
import type { CuratedListSummary, RulesConfig, RunStats, RunStatus, RunStep } from "../types/api";

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(id);
  const { data: users } = useUsers();
  const { data: stats } = useRunStats(id);
  const { data: engines } = useEngines();
  const { data: curatedLists } = useCuratedLists();
  const deleteStep = useDeleteStep(id ?? "");
  const deleteRun = useDeleteRun();
  const updateRun = useUpdateRun(id ?? "");
  const setActiveRun = useActiveRunStore((s) => s.setActiveRun);
  const activeRunId = useActiveRunStore((s) => s.activeRunId);

  // Viewing an in-progress run makes it the "active run" that tools adapt to;
  // a finished run must stop being one so tools can't add films to it.
  useEffect(() => {
    if (!run) return;
    if (run.status === "active") setActiveRun(run.id);
    else if (activeRunId === run.id) setActiveRun(null);
  }, [run?.id, run?.status, activeRunId, setActiveRun]);

  const [activeActor, setActiveActor] = useState<ActorClickPayload | null>(null);
  const [confirmDeleteStepId, setConfirmDeleteStepId] = useState<string | null>(null);
  const [confirmDeleteRun, setConfirmDeleteRun] = useState(false);
  const [confirmForfeit, setConfirmForfeit] = useState(false);

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

  const locked = run.status !== "active";
  const engine = engines?.find((e) => e.game_type === run.game_type);
  // Until /engines loads, assume the classic graph engine can do everything.
  const capabilities =
    engine?.capabilities ?? (run.game_type === "cinechain" ? ["discover_candidates", "solve_bridge"] : []);
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

  async function handleSetStatus(status: RunStatus) {
    await updateRun.mutateAsync({ status });
    setConfirmForfeit(false);
  }

  async function handleDeleteRun() {
    if (!run) return;
    await deleteRun.mutateAsync(run.id);
    navigate("/runs");
  }

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold tracking-tight text-zinc-100">{run.name}</h1>
            <StatusBadge status={run.status} />
            {run.game_type !== "cinechain" && (
              <span className="rounded-full border border-accent/40 bg-accent/10 px-2.5 py-0.5 text-xs font-medium text-accent">
                {engine?.display_name ?? run.game_type}
                {modeDetail(run.game_type, run.rules_config, curatedLists) && (
                  <> &middot; {modeDetail(run.game_type, run.rules_config, curatedLists)}</>
                )}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-zinc-500">{participantNames || "No participants"}</p>
        </div>
        <RunActionsMenu
          canForfeit={!locked}
          onForfeit={() => setConfirmForfeit(true)}
          onDelete={() => setConfirmDeleteRun(true)}
        />
      </div>

      {locked && (
        <RunOutcomeBanner
          status={run.status}
          reason={run.status_reason}
          reopening={updateRun.isPending}
          onReopen={() => handleSetStatus("active")}
        />
      )}

      {run.steps.length === 0 ? (
        <>
          <EmptyState
            icon={Clapperboard}
            title="No films logged yet"
            description={
              locked ? "This run ended before any film was logged." : "Pick the first movie to kick off this run."
            }
          />
          {!locked && (
            <div className="mx-auto mt-2 max-w-md">
              <ActiveFrontierCard
                runId={run.id}
                tailStep={undefined}
                rulesConfig={run.rules_config}
                steps={run.steps}
                locked={locked}
                capabilities={capabilities}
              />
            </div>
          )}
        </>
      ) : (
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-12">
          {/* Right rail: renders first (top) on mobile via source order; pinned
              to the right column on desktop via explicit grid placement. */}
          <div className="order-1 flex flex-col gap-5 lg:order-2 lg:col-span-5 lg:col-start-8 lg:sticky lg:top-20 lg:self-start xl:col-span-4 xl:col-start-9">
            <ActiveFrontierCard
              runId={run.id}
              tailStep={lastStep}
              rulesConfig={run.rules_config}
              steps={run.steps}
              locked={locked}
              capabilities={capabilities}
            />
            <MiniPassportWidget stats={stats} rules={run.rules_config} />
            <RulesSummaryCard runId={run.id} rules={run.rules_config} steps={run.steps} />
          </div>

          <div className="order-2 min-w-0 lg:order-1 lg:col-span-7 lg:col-start-1 xl:col-span-8">
            <ChainTimeline
              runId={run.id}
              steps={run.steps}
              keystoneActorIds={keystoneActorIds}
              onActorClick={(actor) => {
                // Actor forks assume the unconstrained shared-cast engine.
                if (!locked && run.game_type === "cinechain") setActiveActor(actor);
              }}
              onRequestDeleteStep={setConfirmDeleteStepId}
              locked={locked}
            />
          </div>
        </div>
      )}

      {activeActor && !locked && (
        <ForkInTheRoadModal
          open={!!activeActor}
          onClose={() => setActiveActor(null)}
          actorId={activeActor.actorId}
          actorName={activeActor.actorName}
          actorProfilePath={activeActor.profilePath}
          actorCharacterName={activeActor.characterName}
          runId={run.id}
          frontierMovieId={lastStep?.movie_id}
          frontierMovieTitle={lastStep?.movie_title}
          isBrowsingFrontier={activeActor.sourceMovieId === lastStep?.movie_id}
          rulesConfig={run.rules_config}
          steps={run.steps}
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

      <Modal
        open={confirmForfeit}
        onClose={() => setConfirmForfeit(false)}
        title="Forfeit this run?"
        widthClassName="max-w-sm"
      >
        <p className="text-sm text-zinc-400">
          Concede this run as a dead end. Your chain is kept as-is, but no more films can be
          logged unless you reopen it later.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setConfirmForfeit(false)}
            className="rounded-md px-3 py-1.5 text-sm text-zinc-400 hover:bg-app-surface-hover"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={updateRun.isPending}
            onClick={() => handleSetStatus("forfeited")}
            className="flex items-center gap-1.5 rounded-md bg-amber-950 px-3 py-1.5 text-sm font-medium text-amber-300 transition-colors hover:bg-amber-900 disabled:opacity-60"
          >
            {updateRun.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Forfeit Run
          </button>
        </div>
      </Modal>

      <Modal
        open={confirmDeleteRun}
        onClose={() => setConfirmDeleteRun(false)}
        title="Delete this run?"
        widthClassName="max-w-sm"
      >
        <p className="text-sm text-zinc-400">
          Are you sure you want to permanently delete this challenge run? All logged steps and
          history will be lost. This can't be undone.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setConfirmDeleteRun(false)}
            className="rounded-md px-3 py-1.5 text-sm text-zinc-400 hover:bg-app-surface-hover"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={deleteRun.isPending}
            onClick={handleDeleteRun}
            className="flex items-center gap-1.5 rounded-md bg-red-950 px-3 py-1.5 text-sm font-medium text-red-300 transition-colors hover:bg-red-900 disabled:opacity-60"
          >
            {deleteRun.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Delete Permanently
          </button>
        </div>
      </Modal>
    </div>
  );
}

/** Short "what is this run restricted to" label for the header chip. */
function modeDetail(
  gameType: string,
  rules: RulesConfig,
  lists: CuratedListSummary[] | undefined,
): string | null {
  if (gameType === "decade_sieve" && rules.target_decade) return `${rules.target_decade}s`;
  if (gameType === "canon_island" && rules.allowed_curated_list_id) {
    return lists?.find((l) => l.id === rules.allowed_curated_list_id)?.title ?? null;
  }
  return null;
}

const OUTCOME_COPY: Record<
  Exclude<RunStatus, "active">,
  { title: string; fallback: string; icon: typeof Trophy; className: string }
> = {
  completed: {
    title: "Victory!",
    fallback: "This run is complete.",
    icon: Trophy,
    className: "border-emerald-900 bg-emerald-950/60 text-emerald-300",
  },
  failed: {
    title: "Game Over",
    fallback: "This run hit a fail condition.",
    icon: Skull,
    className: "border-red-900 bg-red-950/60 text-red-300",
  },
  forfeited: {
    title: "Run Forfeited",
    fallback: "You conceded this run. Your chain is preserved.",
    icon: Flag,
    className: "border-amber-900 bg-amber-950/50 text-amber-300",
  },
};

function RunOutcomeBanner({
  status,
  reason,
  reopening,
  onReopen,
}: {
  status: RunStatus;
  reason: string | null;
  reopening: boolean;
  onReopen: () => void;
}) {
  if (status === "active") return null;
  const copy = OUTCOME_COPY[status];
  const Icon = copy.icon;

  return (
    <div
      role="status"
      className={cn("mb-5 flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3", copy.className)}
    >
      <Icon className="h-6 w-6 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-base font-semibold">{copy.title}</p>
        <p className="text-sm opacity-80">{reason ?? copy.fallback}</p>
      </div>
      <button
        type="button"
        disabled={reopening}
        onClick={onReopen}
        className="flex items-center gap-1.5 rounded-md border border-current/30 px-3 py-1.5 text-xs font-medium transition-colors hover:bg-white/5 disabled:opacity-60"
      >
        {reopening ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
        Reopen Run
      </button>
    </div>
  );
}

function RunActionsMenu({
  canForfeit,
  onForfeit,
  onDelete,
}: {
  canForfeit: boolean;
  onForfeit: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const itemClass =
    "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-app-surface-hover";

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Run actions"
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 w-8 items-center justify-center rounded-md border border-app-border text-zinc-400 transition-colors hover:bg-app-surface-hover hover:text-zinc-200"
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1.5 w-44 overflow-hidden rounded-lg border border-app-border bg-app-surface py-1 shadow-xl"
        >
          {canForfeit && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onForfeit();
              }}
              className={cn(itemClass, "text-amber-300")}
            >
              <Flag className="h-3.5 w-3.5" />
              Forfeit Run
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
            className={cn(itemClass, "text-red-400")}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete Run
          </button>
        </div>
      )}
    </div>
  );
}

function ActiveFrontierCard({
  runId,
  tailStep,
  rulesConfig,
  steps,
  locked,
  capabilities,
}: {
  runId: string;
  tailStep: RunStep | undefined;
  rulesConfig: RulesConfig;
  steps: RunStep[];
  locked: boolean;
  capabilities: string[];
}) {
  const navigate = useNavigate();
  const [showHub, setShowHub] = useState(false);
  const [showDirectSearch, setShowDirectSearch] = useState(false);
  const isRoulette = capabilities.includes("roulette_spin");
  const canDiscover = capabilities.includes("discover_candidates");
  const canBridge = capabilities.includes("solve_bridge");
  // Modes without a "Pick Next" pool log films through search instead.
  const searchOpen = !canDiscover || showDirectSearch;

  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">
        {isRoulette ? "Movie Night Roulette" : "Active Frontier"}
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

      {locked ? (
        <div className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2.5 text-sm text-zinc-500">
          <Lock className="h-4 w-4 shrink-0" />
          This run is over - logging is locked.
        </div>
      ) : isRoulette ? (
        <RouletteSpinner runId={runId} />
      ) : (
      <div className="flex flex-col gap-2">
        {canDiscover && (
        <button
          type="button"
          onClick={() => setShowHub(true)}
          disabled={!tailStep}
          className="flex items-center justify-center gap-1.5 rounded-md bg-accent px-3.5 py-2.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-4 w-4" />
          Pick Next Movie
        </button>
        )}
        {canBridge && (
        <button
          type="button"
          onClick={() => navigate(`/tools/bridge?run_id=${runId}`)}
          className="flex items-center justify-center gap-1.5 rounded-md border border-app-border px-3.5 py-2.5 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
        >
          <GitBranch className="h-4 w-4" />
          Bridge Solver
        </button>
        )}
        {canDiscover && (
        <button
          type="button"
          onClick={() => setShowDirectSearch((v) => !v)}
          className="text-xs text-zinc-500 transition-colors hover:text-zinc-300"
        >
          Or search for a specific film directly
        </button>
        )}
      </div>
      )}

      {!locked && !isRoulette && searchOpen && (
        <div className="mt-4">
          {!canDiscover && (
            <p className="mb-2 text-xs text-zinc-500">
              Search for a film to log it - this run&apos;s rules are checked as you pick.
            </p>
          )}
          <MovieSearchAutocomplete
            runId={runId}
            tailMovieId={tailStep?.movie_id}
            rulesConfig={rulesConfig}
            steps={steps}
            onLogged={() => setShowDirectSearch(false)}
          />
        </div>
      )}

      {!locked && canDiscover && showHub && tailStep && (
        <PickNextHub
          open={showHub}
          onClose={() => setShowHub(false)}
          runId={runId}
          frontierStep={tailStep}
          rulesConfig={rulesConfig}
          steps={steps}
        />
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

function RulesSummaryCard({
  runId,
  rules,
  steps,
}: {
  runId: string;
  rules: RulesConfig;
  steps: RunStep[];
}) {
  const [showEdit, setShowEdit] = useState(false);
  const wildcardsConsumed = steps.filter(
    (step) => (step.transition_metadata as { wildcard_used?: boolean } | null)?.wildcard_used,
  ).length;

  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">Ruleset</p>
        <button
          type="button"
          onClick={() => setShowEdit(true)}
          className="flex items-center gap-1 text-xs font-medium text-accent hover:underline"
        >
          <Pencil className="h-3 w-3" />
          Edit Rules
        </button>
      </div>
      <div className="flex flex-col gap-2.5 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-zinc-400">Preset</span>
          <span className="capitalize text-zinc-200">{rules.preset}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-zinc-400">Cast Depth</span>
          <span className="text-zinc-200">
            {rules.max_cast_order != null ? `Top ${rules.max_cast_order}` : "—"}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-zinc-400">Repeat Movies</span>
          <span className="text-zinc-200">
            {allowsMovieRepeats(rules) ? "Allowed" : "Disallowed"}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-zinc-400">Wildcard Budget</span>
          <span className="text-zinc-200">
            {rules.wildcards_budget === -1
              ? `${wildcardsConsumed} used / Unlimited`
              : `${wildcardsConsumed} used / ${rules.wildcards_budget} total`}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-zinc-400">Min Runtime</span>
          <span className="text-zinc-200">
            {rules.min_runtime > 0 ? `${rules.min_runtime} min` : "No minimum"}
          </span>
        </div>
      </div>

      {showEdit && (
        <EditRulesModal
          open={showEdit}
          onClose={() => setShowEdit(false)}
          runId={runId}
          currentRules={rules}
        />
      )}
    </div>
  );
}
