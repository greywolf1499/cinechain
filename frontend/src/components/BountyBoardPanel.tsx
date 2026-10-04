import { useEffect, useRef, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import { WILDCARD_REWARD, bountyInfo, completedBountyOf, type BountyInfo } from "../lib/bounties";
import { ApiError } from "../lib/api";
import { cn } from "../lib/cn";
import { useLlmStatus, useRollCustomBounty } from "../lib/queries";
import type { BountyId, RunDetail } from "../types/api";

const CELEBRATION_MS = 5000;

/** "📜 Bounty Board": the three live bounties, each paying a wildcard. A step that completes one
 * triggers a celebration banner and the freshly drawn replacement slides in. */
export default function BountyBoardPanel({ run }: { run: RunDetail }) {
  const [open, setOpen] = useState(true);
  const [celebrating, setCelebrating] = useState<BountyInfo | null>(null);
  const [fresh, setFresh] = useState<BountyId | null>(null);
  const seenSteps = useRef<Set<string> | null>(null);
  const rules = run.rules_config;
  const active = (rules.active_bounties ?? [])
    .map((id) => bountyInfo(id, rules))
    .filter((bounty): bounty is BountyInfo => bounty !== null);
  const { data: llm } = useLlmStatus();
  const roll = useRollCustomBounty(run.id);
  const canRoll = !!llm?.enabled && run.status === "active" && !active.some((b) => b.ai);
  const completedCount = run.rules_config.completed_bounties?.length ?? 0;
  const wildcards = run.rules_config.wildcards_budget;

  // Steps present on first load were completed before this visit: only later ones celebrate.
  useEffect(() => {
    if (seenSteps.current === null) {
      seenSteps.current = new Set(run.steps.map((s) => s.id));
      return;
    }
    const seen = seenSteps.current;
    const added = run.steps.filter((s) => !seen.has(s.id));
    added.forEach((s) => seen.add(s.id));
    const won = added.map((s) => ({ step: s, bounty: completedBountyOf(s, rules) })).find((x) => x.bounty);
    if (!won?.bounty) return;
    setCelebrating(won.bounty);
    const replacement = (won.step.transition_metadata as { bounty_replacement?: BountyId } | null)
      ?.bounty_replacement;
    setFresh(replacement ?? null);
    const timer = window.setTimeout(() => {
      setCelebrating(null);
      setFresh(null);
    }, CELEBRATION_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.steps]);

  return (
    <section
      aria-label="Bounty Board"
      className="mb-5 rounded-xl border border-amber-400/30 bg-amber-500/5 px-4 py-3"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 text-left"
      >
        <span className="text-sm font-semibold text-zinc-100">📜 Bounty Board</span>
        <span className="flex items-center gap-3 text-xs text-zinc-400">
          <span className="font-semibold text-amber-300">
            🎟️ {wildcards === -1 ? "∞" : wildcards} wildcard{wildcards === 1 ? "" : "s"}
          </span>
          {completedCount > 0 && <span>{completedCount} completed</span>}
          <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} />
        </span>
      </button>

      {celebrating && (
        <div
          role="status"
          className="animate-bounty-pop mt-3 flex items-center gap-3 rounded-lg border border-emerald-500/60 bg-emerald-500/10 px-3 py-2.5 shadow-[0_0_24px_-6px_rgba(52,211,153,0.6)]"
        >
          <span className="text-2xl" aria-hidden>
            {celebrating.icon}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-emerald-300">Bounty Complete: {celebrating.title}!</p>
            <p className="text-[11px] text-emerald-200/70">{celebrating.criteria}</p>
          </div>
          <span className="shrink-0 animate-bounce rounded-full bg-amber-400/20 px-2.5 py-1 text-xs font-bold text-amber-200">
            {WILDCARD_REWARD}
          </span>
        </div>
      )}

      {open && (
        <ul className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          {active.map((bounty) => {
            return (
              <li
                key={bounty.id}
                className={cn(
                  "flex flex-col gap-2 rounded-lg border bg-app-bg/70 p-3",
                  bounty.ai ? "border-fuchsia-400/50" : "border-app-border",
                  fresh === bounty.id && "animate-bounty-slide-in border-amber-400/60",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xl" aria-hidden>
                    {bounty.icon}
                  </span>
                  <p className="text-sm font-semibold text-zinc-100">{bounty.title}</p>
                </div>
                {bounty.ai && (
                  <span className="w-fit rounded-full bg-fuchsia-500/15 px-2 py-0.5 text-[10px] font-semibold text-fuchsia-200 ring-1 ring-inset ring-fuchsia-400/40">
                    [ ✨ AI Bounty ]
                  </span>
                )}
                <p className="text-xs text-zinc-400">{bounty.criteria}</p>
                <span className="mt-auto self-start rounded-full bg-amber-400/15 px-2 py-0.5 text-[11px] font-semibold text-amber-200">
                  {WILDCARD_REWARD}
                </span>
              </li>
            );
          })}
          {active.length === 0 && <li className="text-xs text-zinc-500">No open bounties.</li>}
        </ul>
      )}

      {open && canRoll && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={roll.isPending}
            onClick={() => roll.mutate()}
            title="Have the AI write a new bounty to replace the oldest one on the board"
            className="flex items-center gap-1.5 rounded-md border border-fuchsia-400/50 bg-fuchsia-500/10 px-3 py-1.5 text-xs font-semibold text-fuchsia-200 transition-colors hover:bg-fuchsia-500/20 disabled:opacity-60"
          >
            {roll.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <span aria-hidden>✨</span>}
            Roll Custom Bounty
          </button>
          {roll.isError && (
            <span role="alert" className="text-[11px] text-amber-400">
              {roll.error instanceof ApiError ? roll.error.message : "Could not roll a bounty."}
            </span>
          )}
        </div>
      )}
    </section>
  );
}
