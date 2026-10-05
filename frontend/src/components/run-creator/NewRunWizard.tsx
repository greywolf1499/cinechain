import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import Modal from "../Modal";
import { api } from "../../lib/api";
import { useCreateRun, useCuratedLists, useUsers } from "../../lib/queries";
import { useAuthStore } from "../../store/authStore";
import type { EngineMeta, UserSummary } from "../../types/api";
import CreateBlockers from "./CreateBlockers";
import Step1ModeSelect from "./Step1ModeSelect";
import Step2RunSetup from "./Step2RunSetup";
import { initialDraft, useRunDraft } from "./useRunDraft";

export default function NewRunWizard({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (runId: string) => void;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const { data: users } = useUsers();
  const { data: engines } = useQuery({
    queryKey: ["engines"],
    queryFn: () => api.get<EngineMeta[]>("/engines"),
  });
  const { data: curatedLists } = useCuratedLists();
  const currentUser = useAuthStore((state) => state.user);
  const isAdmin = !!currentUser?.is_admin;
  const creator = useCreateRun();
  const runDraft = useRunDraft(engines, isAdmin, curatedLists);
  const {
    draft,
    update,
    reset,
    engine,
    engineSupportsRaw,
    rawEnabled,
    rawParse,
    isTracker,
    canBounty,
    castLinked,
    blockers,
    buildPayload,
    toggleRawMode,
  } = runDraft;
  const unavailableReason = engine?.unavailable_reason;
  const isDirty = JSON.stringify(draft) !== JSON.stringify(initialDraft());

  useEffect(() => {
    if (step === 2 && open) {
      headingRef.current?.focus();
      headingRef.current?.scrollIntoView({ block: "start" });
    }
  }, [step, open]);

  function close() {
    reset();
    creator.reset();
    setStep(1);
    onClose();
  }

  function selectMode(gameType: string) {
    update({ gameType });
  }

  function advanceMode(gameType: string) {
    selectMode(gameType);
    const selected = engines?.find((candidate) => candidate.game_type === gameType);
    if (!selected?.unavailable_reason) setStep(2);
  }

  function onEscape() {
    if (step === 2 && isDirty && !window.confirm("Discard this run setup and close the wizard?")) {
      return true;
    }
    return false;
  }

  function submit() {
    if (blockers.length > 0 || creator.isPending) return;
    creator.mutate(buildPayload(), {
      onSuccess: (run) => {
        reset();
        setStep(1);
        onClose();
        onCreated(run.id);
      },
    });
  }

  function focusBlocker(blocker: string) {
    const target = blocker.includes("run name")
      ? "run-name"
      : blocker.includes("canon list")
        ? draft.gameType === "regional_deep_dive" ? "dive-list" : "canon-list"
        : blocker.includes("Partner A")
          ? "seed-head"
          : blocker.includes("Partner B") || blocker.includes("different starting")
            ? "seed-tail"
            : blocker.includes("bracket")
              ? "bracket-films"
              : blocker.includes("actor")
                ? "actor"
                : blocker.includes("director")
                  ? "director"
                  : blocker.includes("slice")
                      ? "dive-slice"
                    : "raw-rules";
    if (target === "raw-rules") {
      const advancedRules = document.querySelector<HTMLDetailsElement>(
        'details:has([data-blocker-target="raw-rules"])',
      );
      if (advancedRules) advancedRules.open = true;
    }
    const container = document.querySelector<HTMLElement>(`[data-blocker-target="${target}"]`);
    container?.scrollIntoView({ behavior: "smooth", block: "center" });
    container?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
  }

  const owner = users?.find((user: UserSummary) => user.id === currentUser?.id);
  const canAdvance = !unavailableReason;

  return (
    <Modal
      open={open}
      onClose={close}
      onEscape={onEscape}
      title="Start a new run"
      widthClassName="max-w-6xl max-h-[92vh] flex flex-col overflow-hidden"
      bodyClassName="max-h-[70vh] overflow-y-auto p-0"
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs text-zinc-500" aria-label={`Step ${step} of 2`}>
            <span className={step === 1 ? "font-semibold text-accent" : ""}>1 · Mode</span>
            <ArrowRight className="h-3 w-3" />
            <span className={step === 2 ? "font-semibold text-accent" : ""}>2 · Setup</span>
          </div>
          <div className="flex items-center gap-3">
            {step === 1 ? (
              <button
                type="button"
                disabled={!canAdvance}
                onClick={() => setStep(2)}
                className="flex min-h-10 items-center gap-2 rounded-md bg-accent px-4 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
              >
                Next <ArrowRight className="h-4 w-4" />
              </button>
            ) : (
              <>
                <CreateBlockers blockers={blockers} onSelect={focusBlocker} />
                {creator.isError && (
                  <p role="alert" className="max-w-xs text-xs text-red-400">
                    {creator.error instanceof Error ? creator.error.message : "Couldn't create the run."}
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => setStep(1)}
                  className="flex min-h-10 items-center gap-2 rounded-md border border-app-border px-3 text-sm text-zinc-300 hover:text-zinc-100"
                >
                  <ArrowLeft className="h-4 w-4" /> Back
                </button>
                <button
                  type="button"
                  disabled={blockers.length > 0 || creator.isPending}
                  onClick={submit}
                  className="flex min-h-10 items-center gap-2 rounded-md bg-accent px-4 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {creator.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                  Create run
                </button>
              </>
            )}
          </div>
        </div>
      }
    >
      {step === 1 ? (
        <div className="p-5">
          <Step1ModeSelect
            engines={engines}
            gameType={draft.gameType}
            rules={draft.rules}
            onSelect={selectMode}
            onAdvance={advanceMode}
            isAdmin={isAdmin}
          />
        </div>
      ) : (
        <Step2RunSetup
          draft={draft}
          update={update}
          engines={engines}
          users={users}
          owner={owner}
          curatedLists={curatedLists}
          canBounty={canBounty}
          isTracker={isTracker}
          castLinked={castLinked}
          rawEnabled={rawEnabled}
          rawParseError={rawParse.error}
          isAdmin={isAdmin && engineSupportsRaw}
          onToggleRawMode={toggleRawMode}
          onChangeMode={() => setStep(1)}
          headingRef={headingRef}
        />
      )}
    </Modal>
  );
}
