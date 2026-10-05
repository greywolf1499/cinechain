import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Film, Loader2, Plus } from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";
import StatusBadge from "../components/StatusBadge";
import NewRunWizard from "../components/run-creator/NewRunWizard";
import { api } from "../lib/api";
import { useRuns } from "../lib/queries";
import { cn } from "../lib/cn";
import { gameModeStyle } from "../lib/gameModes";
import type { EngineMeta } from "../types/api";

export default function RunsPage() {
  const navigate = useNavigate();
  const { data: runs, isLoading } = useRuns();
  const { data: engines } = useQuery({
    queryKey: ["engines"],
    queryFn: () => api.get<EngineMeta[]>("/engines"),
    staleTime: 5 * 60_000,
  });
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
          description="Choose a movie challenge and invite your household to play."
          action={{ label: "Start your first run", onClick: () => setShowNewRunModal(true), icon: Plus }}
        />
      )}

      {!isLoading && runs && runs.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {runs.map((run) => {
            const style = gameModeStyle(run.game_type);
            const Icon = style.icon;
            const engineName = engines?.find((engine) => engine.game_type === run.game_type)?.display_name;
            return (
              <button
                key={run.id}
                type="button"
                onClick={() => navigate(`/runs/${run.id}`)}
                className="rounded-xl border border-app-border bg-app-surface p-4 text-left transition-colors hover:border-accent/50"
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="truncate text-sm font-semibold text-zinc-100">{run.name}</h3>
                  <StatusBadge status={run.status} />
                </div>
                <span className={cn("mb-2 inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[10px] font-medium", style.bubble)}>
                  <Icon className="h-3 w-3" />
                  {engineName ?? run.game_type.replaceAll("_", " ")}
                </span>
                <p className="text-xs text-zinc-500">
                  Started {new Date(run.created_at).toLocaleDateString()}
                </p>
              </button>
            );
          })}
        </div>
      )}

      <NewRunWizard
        open={showNewRunModal}
        onClose={() => setShowNewRunModal(false)}
        onCreated={(runId) => navigate(`/runs/${runId}`)}
      />
    </div>
  );
}
