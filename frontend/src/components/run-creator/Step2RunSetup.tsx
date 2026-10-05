import ModeOptions from "../ModeOptions";
import RawRulesEditor from "../RawRulesEditor";
import RulesetFields from "../RulesetFields";
import ParticipantPicker from "./ParticipantPicker";
import HeroSeedPreview from "./HeroSeedPreview";
import ModeConfigPanel from "./ModeConfigPanel";
import type { RunDraft } from "./useRunDraft";
import type { CuratedListSummary, EngineMeta, UserSummary } from "../../types/api";
import { gameModeStyle } from "../../lib/gameModes";
import { cn } from "../../lib/cn";
import { Field, inputClass } from "./shared";

export default function Step2RunSetup({
  draft,
  update,
  engines,
  users,
  owner,
  curatedLists,
  canBounty,
  isTracker,
  castLinked,
  rawEnabled,
  rawParseError,
  isAdmin,
  onToggleRawMode,
  onChangeMode,
  headingRef,
}: {
  draft: RunDraft;
  update: (changes: Partial<RunDraft>) => void;
  engines: EngineMeta[] | undefined;
  users: UserSummary[] | undefined;
  owner: UserSummary | null | undefined;
  curatedLists: CuratedListSummary[] | undefined;
  canBounty: boolean;
  isTracker: boolean;
  castLinked: boolean;
  rawEnabled: boolean;
  rawParseError: string | null;
  isAdmin: boolean;
  onToggleRawMode: () => void;
  onChangeMode: () => void;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
}) {
  const mode = engines?.find((engine) => engine.game_type === draft.gameType);
  const rawAvailable = isAdmin && (mode?.capabilities.includes("json_rules") ?? draft.gameType === "cinechain");
  const style = gameModeStyle(draft.gameType);
  const isVersus = draft.gameType === "tug_of_war" || draft.gameType === "rt_split";
  const isTunnel = draft.gameType === "meet_in_the_middle";

  return (
    <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 ref={headingRef} tabIndex={-1} className="text-lg font-semibold text-zinc-100 focus:outline-none">
              Set up your run
            </h3>
            <p className={cn("mt-1 text-sm", style.text)}>{mode?.display_name ?? draft.gameType}</p>
            {mode?.description && <p className="mt-1 text-xs leading-relaxed text-zinc-500">{mode.description}</p>}
          </div>
          <button type="button" onClick={onChangeMode} className="shrink-0 rounded-md border border-app-border px-3 py-1.5 text-xs text-zinc-300 hover:text-zinc-100">
            Change mode
          </button>
        </div>

        <div data-blocker-target="run-name">
          <Field label="Run name">
            <input value={draft.name} onChange={(event) => update({ name: event.target.value })} placeholder="Bacon Sunday" className={inputClass} />
          </Field>
        </div>

        <ParticipantPicker
          users={users}
          selectedIds={draft.participantIds}
          owner={owner}
          showTeams={isVersus}
          onToggle={(userId) => update({
            participantIds: draft.participantIds.includes(userId)
              ? draft.participantIds.filter((id) => id !== userId)
              : [...draft.participantIds, userId],
          })}
        />

        <div
          data-blocker-target={
            draft.gameType === "canon_island" ? "canon-list"
              : draft.gameType === "march_madness" ? "bracket-films"
                : draft.gameType === "method_actor" ? "actor"
                  : draft.gameType === "auteur_marathon" ? "director"
                    : "dive-list"
          }
        >
          <ModeConfigPanel
            gameType={draft.gameType}
            draft={draft}
            update={update}
            curatedLists={curatedLists}
          />
        </div>
        <details className="rounded-lg border border-app-border">
          <summary className="cursor-pointer px-3 py-2.5 text-xs font-semibold text-zinc-300">Advanced rules and options</summary>
          <div className="flex flex-col gap-3 border-t border-app-border p-3">
            {canBounty && (
              <label className={cn(
                "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
                draft.bountyBoard ? "border-amber-400/60 bg-amber-500/10" : "border-app-border bg-app-bg/60",
              )}>
                <input type="checkbox" checked={draft.bountyBoard} onChange={(event) => update({ bountyBoard: event.target.checked })} className="mt-0.5 h-4 w-4 accent-amber-400" />
                <span className="flex flex-col gap-0.5">
                  <span className="text-sm font-semibold text-zinc-100">📜 Bounty Board</span>
                  <span className="text-xs font-normal text-zinc-400">
                    Start with 3 cinephile bounties. Each completed bounty
                    {draft.gameType === "rabbit_hole" ? " restores a life (up to your maximum)." : " earns a wildcard."}
                  </span>
                </span>
              </label>
            )}
            {rawAvailable && (
              <label className="flex cursor-pointer items-center justify-between gap-3 rounded-md border border-app-border px-3 py-2">
                <span className="flex flex-col">
                  <span className="text-xs font-medium text-zinc-300">Raw JSON Override</span>
                  <span className="text-[11px] text-zinc-500">Bypass the form and paste a full ruleset payload.</span>
                </span>
                <button type="button" role="switch" aria-checked={draft.rawMode} onClick={onToggleRawMode} className={cn("relative h-5 w-9 shrink-0 rounded-full transition-colors", draft.rawMode ? "bg-accent" : "bg-app-surface-hover")}>
                  <span className={cn("absolute top-0.5 h-4 w-4 rounded-full bg-zinc-100 transition-all", draft.rawMode ? "left-[18px]" : "left-0.5")} />
                </button>
              </label>
            )}
            {rawEnabled ? (
              <div data-blocker-target="raw-rules">
                <RawRulesEditor text={draft.rawText} onChange={(rawText) => update({ rawText })} />
                {rawParseError && <p className="sr-only" role="alert">{rawParseError}</p>}
              </div>
            ) : (
              !isTracker && (
                <>
                  <ModeOptions
                    gameType={draft.gameType}
                    value={draft.rules}
                    capabilities={mode?.capabilities}
                    onChange={(rules) => update({ rules: { ...draft.rules, ...rules } })}
                  />
                  <RulesetFields
                    value={draft.rules}
                    onChange={(rules) => update({ rules: { ...draft.rules, ...rules } })}
                    castRules={castLinked}
                  />
                </>
              )
            )}
          </div>
        </details>
      </div>

      <div className={cn("flex flex-col gap-3", isTunnel && "lg:col-span-1")}>
        {isTunnel ? (
          <>
            <p className="rounded-md border border-cyan-400/20 bg-cyan-500/5 p-2 text-[11px] text-cyan-200/80">
              Each partner brings a starting film. Extend both chains until one film connects the two ends.
            </p>
            <div data-blocker-target="seed-head">
              <HeroSeedPreview value={draft.seedMovie} onChange={(seedMovie) => update({ seedMovie })} gameType={draft.gameType} label="Partner A's seed" />
            </div>
            <div data-blocker-target="seed-tail">
              <HeroSeedPreview value={draft.tailSeedMovie} onChange={(tailSeedMovie) => update({ tailSeedMovie })} gameType={draft.gameType} label="Partner B's seed" />
            </div>
          </>
        ) : (
          <div data-blocker-target="seed-head">
            <HeroSeedPreview value={draft.seedMovie} onChange={(seedMovie) => update({ seedMovie })} gameType={draft.gameType} label="Seed movie (optional)" />
          </div>
        )}
      </div>
      <span className="sr-only">{rawParseError}</span>
    </div>
  );
}
