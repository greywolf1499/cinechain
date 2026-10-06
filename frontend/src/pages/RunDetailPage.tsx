import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  BookOpen,
  Calendar,
  Clapperboard,
  Drama,
  Flag,
  GitBranch,
  Globe,
  Link2,
  Loader2,
  Lock,
  MoreHorizontal,
  Palette,
  Pencil,
  Plus,
  RotateCcw,
  Skull,
  Sparkles,
  Star,
  Trash2,
  Trophy,
  User,
} from "lucide-react";
import ChainTimeline from "../components/ChainTimeline";
import EditRulesModal from "../components/EditRulesModal";
import ForkInTheRoadModal from "../components/ForkInTheRoadModal";
import Modal from "../components/Modal";
import MoviePoster from "../components/MoviePoster";
import MovieSearchAutocomplete from "../components/MovieSearchAutocomplete";
import PickNextHub from "../components/PickNextHub";
import ModifierChips from "../components/ModifierChips";
import TunnelFrontierCard from "../components/TunnelFrontierCard";
import TunnelTimeline from "../components/TunnelTimeline";
import RouletteSpinner from "../components/RouletteSpinner";
import ForkOfferPanel from "../components/ForkOfferPanel";
import TableSeat from "../components/TableSeat";
import { useTableSeat } from "../lib/tableMode";
import TugOfWarMeter from "../components/TugOfWarMeter";
import PendulumMeter from "../components/PendulumMeter";
import AuteurTrack from "../components/AuteurTrack";
import BountyBoardPanel from "../components/BountyBoardPanel";
import ChaosBanner from "../components/ChaosBanner";
import ChaserPrompt from "../components/ChaserPrompt";
import SplitBoard from "../components/SplitBoard";
import BracketView from "../components/BracketView";
import ExpeditionBoard from "../components/ExpeditionBoard";
import CareerTrack from "../components/CareerTrack";
import RabbitHoleHud from "../components/RabbitHoleHud";
import RabbitHoleGameOver from "../components/RabbitHoleGameOver";
import PlayerAvatar from "../components/PlayerAvatar";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import CountryFlags from "../components/CountryFlags";
import { HowToPlayCard, HowToPlayDrawer } from "../components/HowToPlay";
import { cn } from "../lib/cn";
import { allowsMovieRepeats } from "../lib/rules";
import { useActiveRunStore } from "../store/activeRunStore";
import {
  useCuratedLists,
  useDeleteRun,
  useDeleteStep,
  useEngines,
  useGoldenVeto,
  useRun,
  useRunConstraint,
  useRunStats,
  useRunRulebook,
  useUpdateRun,
  useUpdateRunRules,
  useUsers,
} from "../lib/queries";
import { useAuthStore } from "../store/authStore";
import { TUG_DIMENSIONS, TUG_OF_WAR } from "../lib/tugOfWar";
import { GENRE_PENDULUM } from "../lib/pendulum";
import { RABBIT_HOLE } from "../lib/rabbitHole";
import { MARCH_MADNESS } from "../lib/bracket";
import { AUTEUR_MARATHON } from "../lib/auteurTrack";
import { METHOD_ACTOR } from "../lib/careerTrack";
import { REGIONAL_DEEP_DIVE } from "../lib/expedition";
import { RT_SPLIT } from "../lib/splitScore";
import type { ActorClickPayload } from "../components/actorClickTypes";
import { STANDALONE_MODES, gameModeStyle, usesCastLinks } from "../lib/gameModes";
import { HISTORICAL_TIME_TRAVEL } from "../lib/historicalEra";
import { MEET_IN_THE_MIDDLE } from "../lib/tunnel";
import type {
  CuratedListSummary,
  RulesConfig,
  RunDetail,
  RunStats,
  RunStatus,
  RunStep,
  UserSummary,
} from "../types/api";

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(id);
  const { data: users } = useUsers();
  const currentUser = useAuthStore((s) => s.user);
  const seat = useTableSeat(id ?? "");
  const { data: stats } = useRunStats(id);
  const { data: engines } = useEngines();
  const { data: curatedLists } = useCuratedLists();
  const rulebook = useRunRulebook(id);
  const [showRulebook, setShowRulebook] = useState(false);
  const [introType, setIntroType] = useState<string | null>(null);
  useEffect(() => {
    setShowRulebook(false);
    setIntroType(null);
  }, [id]);
  useEffect(() => {
    const type = run?.game_type;
    if (!type || !rulebook.data) return;
    const key = `cinechain.rulebook.seen.${type}`;
    const seen = localStorage.getItem(key);
    setIntroType(seen ? null : type);
    localStorage.setItem(key, "1");
  }, [id, run?.game_type, !!rulebook.data]);
  const [showDirectSearch, setShowDirectSearch] = useState(false);
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
  const [afterStepDelete, setAfterStepDelete] = useState<(() => void) | null>(null);
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
  const castLinked = usesCastLinks(run.game_type, run.rules_config);
  // A bridge is a chain of shared-cast hops, so it only exists for cast-linked runs.
  const capabilities = (
    engine?.capabilities ?? (run.game_type === "cinechain" ? ["discover_candidates", "solve_bridge"] : [])
  ).filter((capability) => capability !== "solve_bridge" || castLinked);
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
    const afterDelete = afterStepDelete;
    setAfterStepDelete(null);
    afterDelete?.();
  }

  function requestDeleteStep(stepId: string, afterDelete?: () => void) {
    setAfterStepDelete(() => afterDelete ?? null);
    setConfirmDeleteStepId(stepId);
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
              <ModeChip
                gameType={run.game_type}
                label={engine?.display_name ?? run.game_type}
                detail={modeDetail(run.game_type, run.rules_config, curatedLists)}
              />
            )}
          </div>
          <p className="mt-1 text-sm text-zinc-500">{participantNames || "No participants"}</p>
          <TableSeat run={run} users={users} />
        </div>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => {
            localStorage.setItem(`cinechain.rulebook.seen.${run.game_type}`, "1");
            setIntroType(null);
            setShowRulebook(true);
          }}
            className="flex items-center gap-1.5 rounded-lg border border-app-border px-3 py-2 text-xs font-semibold text-zinc-300 hover:text-white">
            <BookOpen className="h-3.5 w-3.5" aria-hidden />
            How to play
          </button>
          <RunActionsMenu
            canForfeit={!locked}
            onForfeit={() => setConfirmForfeit(true)}
            onDelete={() => setConfirmDeleteRun(true)}
          />
        </div>
      </div>
      {introType === run.game_type && rulebook.data && (
        <section aria-label="First visit rules" className="mb-5 rounded-xl border border-sky-500/30 bg-sky-500/5 p-4">
          <HowToPlayCard section={rulebook.data.rulebook} />
          <div className="mt-3 flex gap-4 text-xs">
            <button type="button" className="text-sky-300" onClick={() => setShowRulebook(true)}>Full rules</button>
            <button type="button" className="text-zinc-400" onClick={() => setIntroType(null)}>Got it</button>
          </div>
        </section>
      )}
      <HowToPlayDrawer runId={run.id} engine={engine} open={showRulebook} onClose={() => setShowRulebook(false)} />

      {locked && (
        <RunOutcomeBanner
          status={run.status}
          reason={run.status_reason}
          reopening={updateRun.isPending}
          onReopen={() => handleSetStatus("active")}
        />
      )}

      {run.rules_config.bounty_board && <BountyBoardPanel run={run} />}

      {run.game_type === TUG_OF_WAR && (
        <TugOfWarMeter
          rules={run.rules_config}
          participants={run.participants}
          users={users}
          finished={locked}
        />
      )}

      {run.game_type === RABBIT_HOLE && (
        <>
          {run.status === "failed" && (
            <RabbitHoleGameOver steps={run.steps} rules={run.rules_config} reason={run.status_reason} />
          )}
          <RabbitHoleHud
            runId={run.id}
            rules={run.rules_config}
            depth={run.steps.length}
            finished={locked}
            onSearchManually={() => setShowDirectSearch(true)}
          />
        </>
      )}

      {run.game_type === GENRE_PENDULUM && (
        <PendulumMeter rules={run.rules_config} stepsLogged={run.steps.length} finished={locked} />
      )}

      {!seat?.pending && <ForkOfferPanel run={run} users={users} frontier={lastStep} />}
      {run.game_type !== MEET_IN_THE_MIDDLE && <GoldenVetoBar run={run} users={users} />}

      {run.game_type === MARCH_MADNESS && run.rules_config.bracket ? (
        <BracketView run={run} users={users} currentUserId={currentUser?.id} />
      ) : run.game_type === METHOD_ACTOR && run.rules_config.filmography ? (
        <CareerTrack run={run} />
      ) : run.game_type === AUTEUR_MARATHON && run.rules_config.filmography ? (
        <AuteurTrack run={run} />
      ) : run.game_type === REGIONAL_DEEP_DIVE && run.rules_config.expedition ? (
        <ExpeditionBoard run={run} />
      ) : run.game_type === RT_SPLIT ? (
        <SplitBoard run={run} users={users} />
      ) : run.game_type === MEET_IN_THE_MIDDLE && run.steps.length > 0 ? (
        <div className="flex flex-col gap-6">
          <TunnelTimeline
            runId={run.id}
            steps={run.steps}
            locked={locked}
            onRequestDeleteStep={requestDeleteStep}
          />
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
            <TunnelFrontierCard
              runId={run.id}
              steps={run.steps}
              rulesConfig={run.rules_config}
              locked={locked}
              onRequestDeleteStep={requestDeleteStep}
            />
            <MiniPassportWidget stats={stats} rules={run.rules_config} castLinked={castLinked} />
            <RulesSummaryCard
              runId={run.id}
              rules={run.rules_config}
              steps={run.steps}
              gameType={run.game_type}
              castLinked={castLinked}
            />
          </div>
        </div>
      ) : run.steps.length === 0 ? (
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
                gameType={run.game_type}
                participantCount={run.participants.length}
                showDirectSearch={showDirectSearch}
                onDirectSearchChange={setShowDirectSearch}
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
              gameType={run.game_type}
              participantCount={run.participants.length}
              showDirectSearch={showDirectSearch}
              onDirectSearchChange={setShowDirectSearch}
            />
            <MiniPassportWidget stats={stats} rules={run.rules_config} castLinked={castLinked} />
            <RulesSummaryCard
              runId={run.id}
              rules={run.rules_config}
              steps={run.steps}
              gameType={run.game_type}
              castLinked={castLinked}
            />
          </div>

          <div className="order-2 min-w-0 lg:order-1 lg:col-span-7 lg:col-start-1 xl:col-span-8">
            {run.game_type === "aesthetic_gradient" && <GradientStrip steps={run.steps} />}
            <ChainTimeline
              runId={run.id}
              steps={run.steps}
              keystoneActorIds={keystoneActorIds}
              castLinked={castLinked}
              gameType={run.game_type}
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
        onClose={() => {
          setConfirmDeleteStepId(null);
          setAfterStepDelete(null);
        }}
        title="Remove this step?"
        widthClassName="max-w-sm"
      >
        <p className="text-sm text-zinc-400">
          This removes the most recently logged film from the chain. This can't be undone.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => {
              setConfirmDeleteStepId(null);
              setAfterStepDelete(null);
            }}
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

function ModeChip({ gameType, label, detail }: { gameType: string; label: string; detail: string | null }) {
  const style = gameModeStyle(gameType);
  const Icon = style.icon;
  return (
    <span
      className={cn(
        "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
        style.bubble,
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
      {detail && <> &middot; {detail}</>}
    </span>
  );
}

/** Short "what is this run restricted to" label for the header chip. */
function modeDetail(
  gameType: string,
  rules: RulesConfig,
  lists: CuratedListSummary[] | undefined,
): string | null {
  if (gameType === "chrono_climb") {
    const label = rules.direction === "descent" ? "Descent" : "Climb";
    return rules.require_cast_link ? `${label} + cast link` : label;
  }
  if (gameType === HISTORICAL_TIME_TRAVEL) {
    const label = rules.direction === "descent" ? "Backward" : "Forward";
    return rules.require_cast_link ? `${label} + cast link` : label;
  }
  if (STANDALONE_MODES.has(gameType) && rules.require_cast_link) return "+ cast link";
  if (gameType === "decade_sieve" && rules.target_decade) return `${rules.target_decade}s`;
  if (gameType === GENRE_PENDULUM) {
    return (rules.genre_cycle ?? []).slice(0, 4).join(" → ") + ((rules.genre_cycle?.length ?? 0) > 4 ? " …" : "");
  }
  if (gameType === TUG_OF_WAR) {
    return `${TUG_DIMENSIONS[rules.dimension ?? "era"].label}, lead of ${rules.target_lead ?? 4}`;
  }
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
  gameType,
  participantCount,
  showDirectSearch,
  onDirectSearchChange,
}: {
  runId: string;
  tailStep: RunStep | undefined;
  rulesConfig: RulesConfig;
  steps: RunStep[];
  locked: boolean;
  capabilities: string[];
  gameType: string;
  participantCount: number;
  showDirectSearch: boolean;
  onDirectSearchChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const [showHub, setShowHub] = useState(false);
  const [chaserHub, setChaserHub] = useState(false);
  const [showForkHub, setShowForkHub] = useState(false);
  const forkEnabled = !!rulesConfig.blind_fork;
  const forkPending = !!rulesConfig.pending_fork;
  const canFork = capabilities.includes("discover_candidates") && !capabilities.includes("tunnel");
  const { data: constraint } = useRunConstraint(runId);
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

      {constraint && !locked && (
        <div role="note" className="mb-3 rounded-md border border-accent/30 bg-accent/5 px-3 py-2">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-accent">
            <ConstraintIcon kind={constraint.kind} />
            {constraint.title}
          </p>
          {constraint.detail && <p className="mt-0.5 text-[11px] text-zinc-500">{constraint.detail}</p>}
          <div className="mt-1.5 empty:hidden">
            <ModifierChips constraint={constraint} />
          </div>
        </div>
      )}

      {!locked && canDiscover && rulesConfig.active_chaos && (
        <ChaosBanner runId={runId} chaos={rulesConfig.active_chaos} className="mb-3" />
      )}

      {!locked && canFork && tailStep && (
        <BlindForkToggle runId={runId} rules={rulesConfig} participantCount={participantCount} />
      )}

      {locked ? (
        <div className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2.5 text-sm text-zinc-500">
          <Lock className="h-4 w-4 shrink-0" />
          This run is over - logging is locked.
        </div>
      ) : forkPending ? (
        <div className="flex items-center gap-2 rounded-md border border-fuchsia-400/30 bg-fuchsia-500/5 px-3 py-2.5 text-sm text-fuchsia-200">
          <Lock className="h-4 w-4 shrink-0" />
          Logging is paused while a Blind Fork offer is answered.
        </div>
      ) : isRoulette ? (
        <RouletteSpinner runId={runId} />
      ) : (
      <div className="flex flex-col gap-2">
        {canDiscover && forkEnabled && canFork && participantCount > 1 && (
        <button
          type="button"
          onClick={() => setShowForkHub(true)}
          disabled={!tailStep}
          className="flex items-center justify-center gap-1.5 rounded-md bg-fuchsia-400 px-3.5 py-2.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-fuchsia-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span aria-hidden>🎭</span>
          Offer 3 Films (Blind Fork)
        </button>
        )}
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
        {canDiscover && tailStep && (
          <ChaserPrompt
            frontier={tailStep}
            onGrab={() => {
              setChaserHub(true);
              setShowHub(true);
            }}
          />
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
          onClick={() => onDirectSearchChange(!showDirectSearch)}
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
            onLogged={() => onDirectSearchChange(false)}
          />
        </div>
      )}

      {!locked && canDiscover && showForkHub && tailStep && (
        <PickNextHub
          open={showForkHub}
          onClose={() => setShowForkHub(false)}
          runId={runId}
          frontierStep={tailStep}
          rulesConfig={rulesConfig}
          steps={steps}
          gameType={gameType}
          forkMode
        />
      )}

      {!locked && canDiscover && showHub && tailStep && (
        <PickNextHub
          open={showHub}
          onClose={() => {
            setShowHub(false);
            setChaserHub(false);
          }}
          runId={runId}
          frontierStep={tailStep}
          rulesConfig={rulesConfig}
          steps={steps}
          gameType={gameType}
          initialChaser={chaserHub}
        />
      )}
    </div>
  );
}

function BlindForkToggle({
  runId,
  rules,
  participantCount,
}: {
  runId: string;
  rules: RulesConfig;
  participantCount: number;
}) {
  const updateRules = useUpdateRunRules(runId);
  const enabled = !!rules.blind_fork;

  return (
    <div className="mb-4 rounded-md border border-app-border bg-app-bg/60 px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <span className="flex flex-col">
          <span className="text-xs font-medium text-zinc-200">Enable Blind Fork (Offer 3, Veto 1)</span>
          <span className="text-[11px] text-zinc-500">
            {participantCount < 2
              ? "Needs a partner on the run to answer your offers."
              : "Offer your partner three films; they veto one and watch one of the other two."}
          </span>
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label="Enable Blind Fork"
          disabled={updateRules.isPending || (participantCount < 2 && !enabled)}
          onClick={() => updateRules.mutate({ ...rules, blind_fork: !enabled })}
          className={cn(
            "relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50",
            enabled ? "bg-accent" : "bg-app-surface-hover",
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 h-4 w-4 rounded-full bg-zinc-100 transition-all",
              enabled ? "left-[18px]" : "left-0.5",
            )}
          />
        </button>
      </div>
      {updateRules.isError && (
        <p role="alert" className="mt-1.5 text-[11px] text-red-400">
          {updateRules.error instanceof Error ? updateRules.error.message : "Couldn't change the setting."}
        </p>
      )}
    </div>
  );
}

/** Golden Veto: overrule the partner's latest step (a monthly token). Fork offers are vetoed from
 * the offer overlay instead. */
function GoldenVetoBar({ run, users }: { run: RunDetail; users: UserSummary[] | undefined }) {
  const me = useAuthStore((s) => s.user);
  const seat = useTableSeat(run.id);
  const actorId = run.rules_config.table_mode ? seat?.id : me?.id;
  const goldenVeto = useGoldenVeto(run.id);
  const [confirming, setConfirming] = useState(false);
  const seeds = run.game_type === MEET_IN_THE_MIDDLE ? 2 : 1;
  const latest = run.steps[run.steps.length - 1];
  const loggerId = latest?.transition_metadata?.acting_participant_id as string | undefined ?? latest?.logged_by_user_id;
  const players = run.rules_config.tug_players;
  const latestTeam = latest?.transition_metadata?.tug_team;
  const actorTeam = Object.entries(players ?? {}).find(([, id]) => id === actorId)?.[0];
  const partnerLogged = run.game_type === TUG_OF_WAR
    ? !!latestTeam && !!actorTeam && latestTeam !== actorTeam
    : !!loggerId && loggerId !== actorId;
  if (run.status !== "active" || run.steps.length <= seeds || !partnerLogged || run.rules_config.pending_fork) {
    return null;
  }
  const tokens = run.rules_config.table_mode ? users?.find((u) => u.id === actorId)?.veto_tokens ?? 0 : me?.veto_tokens ?? 0;
  const pullOwnerId = latestTeam === "team_a" || latestTeam === "team_b" ? players?.[latestTeam] : undefined;
  const partnerName = users?.find((u) => u.id === (pullOwnerId ?? loggerId))?.display_name ?? "Your partner";

  return (
    <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-amber-700/40 bg-amber-950/20 px-4 py-2.5">
      <PlayerAvatar
        name={partnerName}
        size="sm"
        className="border-amber-500/60 bg-amber-500/10 text-amber-200"
      />
      <p className="min-w-0 flex-1 text-xs text-zinc-400">
        <strong className="text-zinc-200">{partnerName}</strong>{run.game_type === TUG_OF_WAR ? "'s pull used " : " logged "}
        <strong className="text-zinc-200">{latest.movie_title}</strong>. Not having it?
        {goldenVeto.isError && (
          <span role="alert" className="ml-1 text-red-400">
            {goldenVeto.error instanceof Error ? goldenVeto.error.message : "Couldn't veto."}
          </span>
        )}
      </p>
      {confirming ? (
        <span className="flex items-center gap-2">
          <button
            type="button"
            disabled={goldenVeto.isPending}
            onClick={() => goldenVeto.mutate("step", { onSettled: () => setConfirming(false) })}
            className="flex items-center gap-1.5 rounded-md bg-amber-500 px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-amber-400 disabled:opacity-60"
          >
            {goldenVeto.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Spend token
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="rounded-md px-2 py-1.5 text-xs text-zinc-400 hover:bg-app-surface-hover"
          >
            Cancel
          </button>
        </span>
      ) : (
        <button
          type="button"
          disabled={tokens < 1}
          onClick={() => setConfirming(true)}
          title={tokens < 1 ? "No Golden Veto tokens left - you get one every 30 days" : undefined}
          className="flex items-center gap-1.5 rounded-md border border-amber-700/60 px-3 py-1.5 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-900/40 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Star className="h-3.5 w-3.5" />
          Golden Veto ({tokens} left)
        </button>
      )}
    </div>
  );
}

/** The run's poster colours blended left-to-right: the "gradient" an Aesthetic Gradient run draws. */
function GradientStrip({ steps }: { steps: RunStep[] }) {
  const colors = steps.map((s) => s.movie_dominant_color).filter((c): c is string => !!c);
  if (colors.length === 0) return null;
  const background =
    colors.length === 1 ? colors[0] : `linear-gradient(to right, ${colors.join(", ")})`;
  return (
    <div className="mb-3" aria-label="Run colour gradient">
      <div className="h-3 rounded-full border border-app-border" style={{ background }} />
      <p className="mt-1 text-[10px] text-zinc-500">
        Colour gradient across {colors.length} poster{colors.length === 1 ? "" : "s"}
      </p>
    </div>
  );
}

function ConstraintIcon({ kind }: { kind: string }) {
  const className = "h-3.5 w-3.5 shrink-0";
  switch (kind) {
    case "director":
      return <Clapperboard className={className} />;
    case "actor":
      return <User className={className} />;
    case "year":
      return <Calendar className={className} />;
    case "country":
      return <Globe className={className} />;
    case "color":
      return <Palette className={className} />;
    case "semantic":
      return <Sparkles className={className} />;
    case "genre":
      return <Drama className={className} />;
    default:
      return <Link2 className={className} />;
  }
}

function MiniPassportWidget({
  stats,
  rules,
  castLinked,
}: {
  stats: RunStats | undefined;
  rules: RulesConfig;
  castLinked: boolean;
}) {
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
                <CountryFlags codes={stats.countries} />
              )}
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Decades</span>
            <span className="text-zinc-200">{stats.decades.length}</span>
          </div>
          {castLinked && (
            <div className="flex items-center justify-between gap-2">
              <span className="shrink-0 text-zinc-400">Keystone Actor</span>
              <span className="max-w-[65%] truncate rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
                {topActor ? topActor.actor_name : "—"}
              </span>
            </div>
          )}
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
  gameType,
  castLinked,
}: {
  runId: string;
  rules: RulesConfig;
  steps: RunStep[];
  gameType: string;
  castLinked: boolean;
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
        {gameType === "chrono_climb" && (
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Direction</span>
            <span className="text-zinc-200">
              {rules.direction === "descent" ? "▼ Descent (older each film)" : "▲ Climb (newer each film)"}
            </span>
          </div>
        )}
        {gameType === HISTORICAL_TIME_TRAVEL && (
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Direction</span>
            <span className="text-zinc-200">
              {rules.direction === "descent"
                ? "⏪ Backward (set earlier each film)"
                : "⏩ Forward (set later each film)"}
            </span>
          </div>
        )}
        {STANDALONE_MODES.has(gameType) && (
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Shared Cast</span>
            <span className="text-zinc-200">{castLinked ? "Required (hybrid)" : "Not required"}</span>
          </div>
        )}
        {castLinked && (
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Cast Depth</span>
            <span className="text-zinc-200">
              {rules.max_cast_order != null ? `Top ${rules.max_cast_order}` : "—"}
            </span>
          </div>
        )}
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
          gameType={gameType}
        />
      )}
    </div>
  );
}
