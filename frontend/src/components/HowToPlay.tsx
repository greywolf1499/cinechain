import { useEffect, useId, useRef, useState } from "react";
import Modal from "./Modal";
import Popover from "./ui/Popover";
import { useEngines, useRunRulebook } from "../lib/queries";
import type { EngineMeta, RuleSection } from "../types/api";

export function HowToPlayCard({ section }: { section: RuleSection }) {
  return (
    <dl className="space-y-2 text-xs text-zinc-300">
      {[
        ["Goal", section.goal],
        ["Your turn", section.turn[0]],
        ["How to win", section.scoring[0]],
      ].map(([label, text]) => (
        <div key={label}>
          <dt className="font-semibold text-zinc-100">{label}</dt>
          <dd>{text}</dd>
        </div>
      ))}
    </dl>
  );
}

export function GlossaryChip({
  term, children, glossary, className = "",
}: {
  term: string;
  children: React.ReactNode;
  glossary?: Record<string, string>;
  className?: string;
}) {
  const anchor = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const { data: engines, error } = useEngines();
  const definition = glossary?.[term] ?? engines?.flatMap((engine) =>
    engine.glossary?.[term] ? [engine.glossary[term]] : [],
  )[0];
  return (
    <>
      <button ref={anchor} type="button" aria-expanded={open}
        aria-label={`Explain ${term.replaceAll("_", " ")}`}
        className={`rounded underline decoration-dotted underline-offset-4 ${className}`}
        onClick={() => setOpen(!open)}>
        {children}
      </button>
      <Popover anchorRef={anchor} open={open} onClose={() => setOpen(false)}
        label={`Glossary: ${term}`} matchAnchorWidth={false} className="max-w-xs p-3 text-xs text-zinc-300">
        {definition ?? (error ? "Could not load the glossary. Try again." : "Loading glossary...")}
      </Popover>
    </>
  );
}

function FullSection({ section, glossary }: { section: RuleSection; glossary: Record<string, string> }) {
  return (
    <div className="space-y-4 text-sm text-zinc-300">
      <p className="font-semibold text-zinc-100">{section.goal}</p>
      {([
        ["Your turn", section.turn], ["Scoring and progress", section.scoring],
        ["How it ends", section.lose], ["Strategy", section.tips],
      ] as const).map(([title, lines]) => lines.length > 0 && (
        <section key={title}>
          <h3 className="mb-1 font-semibold text-zinc-100">{title}</h3>
          <ul className="list-inside list-disc space-y-1">{lines.map((line) => <li key={line}>{line}</li>)}</ul>
        </section>
      ))}
      <div className="flex flex-wrap gap-3" aria-label="Glossary">
        {section.glossary.map((term) => (
          <GlossaryChip key={term} term={term} glossary={glossary}>{term.replaceAll("_", " ")}</GlossaryChip>
        ))}
      </div>
    </div>
  );
}

export function HowToPlayDrawer({
  open, onClose, engine, runId,
}: { open: boolean; onClose: () => void; engine?: EngineMeta; runId?: string }) {
  const { data, error, isFetching, refetch } = useRunRulebook(runId);
  const [tab, setTab] = useState<"rules" | "settings">("rules");
  const tabId = useId();
  useEffect(() => { setTab("rules"); }, [runId, engine?.game_type, open]);
  const section = runId ? data?.rulebook : engine?.rulebook;
  const glossary = data?.glossary ?? engine?.glossary ?? {};
  return (
    <Modal open={open} onClose={onClose} title={`How to play: ${data?.display_name ?? engine?.display_name ?? "this run"}`}
      widthClassName="ml-auto h-full max-w-xl rounded-l-xl" bodyClassName="max-h-[80vh] overflow-y-auto px-5 py-4">
      {runId && (
        <div className="mb-4 flex gap-3" role="tablist" aria-label="Rulebook view">
          {(["rules", "settings"] as const).map((value) => (
            <button key={value} type="button" role="tab" aria-selected={tab === value}
              id={`${tabId}-${value}`} aria-controls={`${tabId}-panel`} tabIndex={tab === value ? 0 : -1}
              className={tab === value ? "font-semibold text-sky-300" : "text-zinc-400"}
              onKeyDown={(event) => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === "Home" ? "rules" : event.key === "End" ? "settings"
                  : tab === "rules" ? "settings" : "rules";
                setTab(next);
                const buttons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
                buttons?.[next === "rules" ? 0 : 1].focus();
              }}
              onClick={() => setTab(value)}>{value === "rules" ? "How to play" : "This run"}</button>
          ))}
        </div>
      )}
      <div id={`${tabId}-panel`} role={runId ? "tabpanel" : undefined}
        aria-labelledby={runId ? `${tabId}-${tab}` : undefined}>
      {error ? (
        <div role="alert" className="text-sm text-red-300">
          Could not load this run's rules. <button type="button" disabled={isFetching} onClick={() => refetch()}>Retry</button>
        </div>
      ) : !section ? <p className="text-sm text-zinc-400">Loading rulebook...</p> : tab === "settings" ? (
        <dl className="space-y-3 text-sm">
          {Object.entries(data?.settings ?? {}).map(([label, value]) => (
            <div key={label}><dt className="font-semibold text-zinc-200">{label}</dt><dd className="text-zinc-400">{value}</dd></div>
          ))}
          <dt className="font-semibold text-zinc-200">Active overlays</dt>
          <dd className="text-zinc-400">{data?.overlays.map((overlay) => overlay.title).join(", ") || "None"}</dd>
        </dl>
      ) : (
        <>
          <FullSection section={section} glossary={glossary} />
          {data?.overlays.map((overlay) => (
            <section key={overlay.key} className="mt-5 border-t border-app-border pt-4">
              <h2 className="mb-3 font-semibold text-sky-300">{overlay.title}</h2>
              <FullSection section={overlay.rulebook} glossary={glossary} />
            </section>
          ))}
        </>
      )}
      </div>
    </Modal>
  );
}

export function ModeRulebookHelp({ engine }: { engine: EngineMeta }) {
  const anchor = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(false);
  return (
    <>
      <button ref={anchor} type="button" aria-label={`How to play ${engine.display_name}`}
        aria-expanded={open} className="absolute right-2 top-2 rounded-full border border-zinc-600 bg-app-surface px-2 text-sm text-zinc-300 hover:text-white"
        onClick={() => setOpen(!open)}>?</button>
      <Popover anchorRef={anchor} open={open} onClose={() => setOpen(false)}
        label={`How to play ${engine.display_name}`} matchAnchorWidth={false} className="w-80 max-w-[90vw] p-4">
        {engine.rulebook ? <HowToPlayCard section={engine.rulebook} /> : <p>Loading rulebook...</p>}
        <button type="button" className="mt-3 text-xs font-semibold text-sky-300"
          onClick={() => { setOpen(false); setFull(true); }}>Full rules</button>
      </Popover>
      <HowToPlayDrawer engine={engine} open={full} onClose={() => { setFull(false); anchor.current?.focus(); }} />
    </>
  );
}
