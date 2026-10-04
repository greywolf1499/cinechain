import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Film, Loader2, Plus } from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";
import StatusBadge from "../components/StatusBadge";
import Modal from "../components/Modal";
import GameModePicker from "../components/GameModePicker";
import ModeOptions from "../components/ModeOptions";
import SeedMoviePicker from "../components/SeedMoviePicker";
import RawRulesEditor, { RAW_RULES_EXAMPLE, parseRawRules } from "../components/RawRulesEditor";
import RulesetFields, { RULE_PRESETS } from "../components/RulesetFields";
import { api } from "../lib/api";
import { useCreateRun, useCuratedLists, useRuns, useUsers } from "../lib/queries";
import { cn } from "../lib/cn";
import { STANDALONE_MODES, usesCastLinks } from "../lib/gameModes";
import { useAuthStore } from "../store/authStore";
import type { EngineMeta, MovieSummary, RulesConfig } from "../types/api";

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
  const { data: curatedLists } = useCuratedLists();
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);

  const [name, setName] = useState("");
  const [gameType, setGameType] = useState("cinechain");
  const [participantIds, setParticipantIds] = useState<string[]>([]);
  const [seedMovie, setSeedMovie] = useState<MovieSummary | null>(null);
  const [rules, setRules] = useState<RulesConfig>(RULE_PRESETS.standard);
  const [canonListId, setCanonListId] = useState("");
  const [targetDecade, setTargetDecade] = useState(1970);
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState("");

  // Admin-only "Super-Unlock", and only for engines that honour JSON rulesets.
  const engineSupportsRaw =
    engines?.find((engine) => engine.game_type === gameType)?.capabilities.includes("json_rules") ??
    gameType === "cinechain";
  const rawEnabled = rawMode && isAdmin && engineSupportsRaw;
  const rawParse = parseRawRules(rawText);

  const selectedEngine = engines?.find((engine) => engine.game_type === gameType);
  // Modes without Pick Next discovery are SQL trackers rather than cast-graph chains.
  const isTracker = !!selectedEngine && !selectedEngine.capabilities.includes("discover_candidates");
  const needsCanonList = gameType === "canon_island";
  const needsDecade = gameType === "decade_sieve";
  // A canon list with no synced films would block every pick.
  const islandLists = (curatedLists ?? []).filter((list) => list.is_enabled && list.total_items > 0);

  const isStandalone = STANDALONE_MODES.has(gameType);
  const castLinked = usesCastLinks(gameType, rules);
  const formRules: RulesConfig = {
    ...(isTracker ? TRACKER_RULES : rules),
    ...(isStandalone ? { require_cast_link: !!rules.require_cast_link } : {}),
    ...(gameType === "chrono_climb" ? { direction: rules.direction ?? "climb" } : {}),
    ...(needsCanonList ? { allowed_curated_list_id: canonListId } : {}),
    ...(needsDecade ? { target_decade: targetDecade } : {}),
  };
  const missingMode = needsCanonList && !canonListId;

  function toggleRawMode() {
    if (!rawMode && !rawText.trim()) {
      // Seed with the form's current values plus an example condition pair.
      setRawText(JSON.stringify({ ...RAW_RULES_EXAMPLE, ...formRules }, null, 2));
    }
    setRawMode((v) => !v);
  }

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
    setRules(RULE_PRESETS.standard);
    setCanonListId("");
    setTargetDecade(1970);
    setRawMode(false);
    setRawText("");
    createRun.reset();
  }

  async function handleSubmit() {
    if (!name.trim()) return;
    if (rawEnabled && rawParse.error !== null) return;
    if (missingMode && !rawEnabled) return;
    const run = await createRun.mutateAsync({
      name: name.trim(),
      game_type: gameType,
      participant_user_ids: participantIds,
      seed_movie_id: seedMovie?.tmdb_id ?? null,
      rules_config: rawEnabled && rawParse.value ? rawParse.value : formRules,
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
      widthClassName="max-w-4xl"
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

        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-zinc-400">Game mode</span>
          <GameModePicker engines={engines} value={gameType} onChange={setGameType} />
        </div>

        {needsCanonList && (
          <Field label="Canon list (every film must be on it)">
            <select
              value={canonListId}
              onChange={(e) => setCanonListId(e.target.value)}
              className={inputClass}
            >
              <option value="">Choose a list...</option>
              {islandLists.map((list) => (
                <option key={list.id} value={list.id}>
                  {list.title} ({list.total_items} films)
                </option>
              ))}
            </select>
            {islandLists.length === 0 && (
              <span className="text-[11px] font-normal text-amber-400">
                No synced lists yet - enable and sync one under Lists first.
              </span>
            )}
          </Field>
        )}

        {needsDecade && (
          <Field label="Decade (every film must be released in it)">
            <select
              value={targetDecade}
              onChange={(e) => setTargetDecade(Number(e.target.value))}
              className={inputClass}
            >
              {DECADES.map((decade) => (
                <option key={decade} value={decade}>
                  {decade}s
                </option>
              ))}
            </select>
          </Field>
        )}

        {isStandalone && <ModeOptions gameType={gameType} value={rules} onChange={setRules} />}

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

        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-zinc-400">Seed movie (optional)</span>
          <SeedMoviePicker value={seedMovie} onChange={setSeedMovie} gameType={gameType} />
        </div>

        {isAdmin && engineSupportsRaw && (
          <label className="flex cursor-pointer items-center justify-between gap-3 rounded-md border border-app-border px-3 py-2">
            <span className="flex flex-col">
              <span className="text-xs font-medium text-zinc-300">Raw JSON Override</span>
              <span className="text-[11px] text-zinc-500">
                Bypass the form and paste a full ruleset payload.
              </span>
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={rawMode}
              onClick={toggleRawMode}
              className={cn(
                "relative h-5 w-9 shrink-0 rounded-full transition-colors",
                rawMode ? "bg-accent" : "bg-app-surface-hover",
              )}
            >
              <span
                className={cn(
                  "absolute top-0.5 h-4 w-4 rounded-full bg-zinc-100 transition-all",
                  rawMode ? "left-[18px]" : "left-0.5",
                )}
              />
            </button>
          </label>
        )}

        {rawEnabled ? (
          <RawRulesEditor text={rawText} onChange={setRawText} />
        ) : (
          !isTracker && (
            <RulesetFields
              value={rules}
              onChange={(next) => setRules({ ...rules, ...next })}
              castRules={castLinked}
            />
          )
        )}

        {createRun.isError && (
          <p role="alert" className="text-xs text-red-400">
            {createRun.error instanceof Error ? createRun.error.message : "Couldn't create the run."}
          </p>
        )}

        <button
          type="button"
          disabled={
            !name.trim() ||
            createRun.isPending ||
            (rawEnabled ? rawParse.error !== null : missingMode)
          }
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

// Tracker modes have no shared-cast graph, so the cast-depth / wildcard form doesn't apply.
const TRACKER_RULES: RulesConfig = {
  preset: "custom",
  allow_repeats: "strict",
  no_consecutive_actor: false,
  max_cast_order: 15,
  min_runtime: 0,
  wildcards_budget: 0,
};

const LATEST_DECADE = Math.floor(new Date().getFullYear() / 10) * 10;
const DECADES = Array.from({ length: (LATEST_DECADE - 1890) / 10 + 1 }, (_, i) => LATEST_DECADE - i * 10);

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
