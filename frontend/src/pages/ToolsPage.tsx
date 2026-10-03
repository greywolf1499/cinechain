import { useEffect } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Link2, Lock, X } from "lucide-react";
import PageHeading from "../components/PageHeading";
import { ApiError } from "../lib/api";
import { useEngines, useRun } from "../lib/queries";
import { TOOLS, toolCompatibility, type Compatibility, type ToolDefinition } from "../lib/tools";
import { cn } from "../lib/cn";
import { useActiveRunStore } from "../store/activeRunStore";

export default function ToolsPage() {
  const activeRunId = useActiveRunStore((s) => s.activeRunId);
  const setActiveRun = useActiveRunStore((s) => s.setActiveRun);
  const { data: run, error } = useRun(activeRunId ?? undefined);
  const { data: engines } = useEngines();

  // A deleted run (or one we can no longer see) must not keep steering the tools.
  useEffect(() => {
    if (error instanceof ApiError && error.status === 404) setActiveRun(null);
  }, [error, setActiveRun]);
  const activeRun = run && run.status === "active" ? run : null;

  return (
    <div>
      <PageHeading title="Tools" subtitle="Utilities for exploring films and building challenges" />

      {activeRun && (
        <div className="mb-5 flex flex-wrap items-center gap-2.5 rounded-xl border border-accent/40 bg-accent/10 px-5 py-3 text-sm text-accent">
          <Link2 className="h-4 w-4 shrink-0" />
          <span>
            Active run: <strong>{activeRun.name}</strong>. Tools adapt to it where they can.
          </span>
          <Link to={`/runs/${activeRun.id}`} className="underline">
            Open run
          </Link>
          <button
            type="button"
            onClick={() => setActiveRun(null)}
            className="ml-auto flex items-center gap-1 text-xs hover:underline"
          >
            <X className="h-3.5 w-3.5" /> Clear
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {TOOLS.map((tool) => (
          <ToolCard
            key={tool.id}
            tool={tool}
            compatibility={toolCompatibility(tool, activeRun, engines)}
            runId={activeRun?.id}
          />
        ))}
      </div>
    </div>
  );
}

function ToolCard({
  tool,
  compatibility,
  runId,
}: {
  tool: ToolDefinition;
  compatibility: Compatibility;
  runId: string | undefined;
}) {
  const Icon = tool.icon;
  const comingSoon = tool.to === null;
  const disabled = comingSoon || compatibility.state === "incompatible";
  const tooltip = comingSoon ? "Coming soon" : compatibility.message;
  const href = tool.to && runId && compatibility.state === "compatible" ? `${tool.to}?run_id=${runId}` : tool.to;

  const body = (
    <>
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-app-surface-hover text-accent">
          <Icon className="h-5 w-5" />
        </span>
        <h2 className="text-sm font-semibold text-zinc-100">{tool.name}</h2>
      </div>
      <p className="text-xs leading-relaxed text-zinc-500">{tool.description}</p>
      <div className="mt-auto flex items-center gap-1.5 text-[11px] font-medium">
        {comingSoon && (
          <span className="rounded-full border border-app-border px-2 py-0.5 text-zinc-500">Coming soon</span>
        )}
        {compatibility.state === "compatible" && (
          <span className="flex items-center gap-1 text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5" /> Compatible with current run
          </span>
        )}
        {compatibility.state === "incompatible" && (
          <span className="flex items-center gap-1 text-amber-400">
            <Lock className="h-3.5 w-3.5" /> Incompatible with this run
          </span>
        )}
      </div>
      {tooltip && (
        <span
          role="tooltip"
          className="pointer-events-none absolute -top-2 left-4 z-10 max-w-[90%] -translate-y-full rounded-md border border-app-border bg-app-bg px-2.5 py-1.5 text-[11px] text-zinc-200 opacity-0 shadow-xl shadow-black/50 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
        >
          {tooltip}
        </span>
      )}
    </>
  );

  const className = cn(
    "group relative flex min-h-36 flex-col gap-3 rounded-xl border bg-app-surface p-5 text-left outline-none",
    disabled
      ? "cursor-not-allowed border-app-border opacity-60"
      : "border-app-border transition-colors hover:bg-app-surface-hover focus-visible:border-accent",
    compatibility.state === "compatible" && "border-emerald-700/60",
  );

  return disabled || !href ? (
    <div className={className} aria-disabled="true" tabIndex={0}>
      {body}
    </div>
  ) : (
    <Link to={href} className={className}>
      {body}
    </Link>
  );
}
