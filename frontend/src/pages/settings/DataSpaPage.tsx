import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Database, Loader2, Sparkles, Wand2 } from "lucide-react";
import EmptyState from "../../components/EmptyState";
import TaskProgressBar from "../../components/TaskProgressBar";
import { SettingsCard, inputClass } from "../../components/settings/shared";
import { useCacheHealth } from "../../lib/queries";
import { api } from "../../lib/api";
import { TASK_TITLES, describeProgress, taskPausedReason } from "../../lib/tasks";
import { useTrackedTask } from "../../lib/useTrackedTask";
import { useAuthStore } from "../../store/authStore";
import type { CoverageCount, IntegrationConfig, SpaTreatment } from "../../types/api";

const DEFAULT_BATCH_CAP = 200;
const MAX_BATCH_CAP = 2000;

const TREATMENTS: { key: Exclude<SpaTreatment, "fix_all">; label: string; hint: string }[] = [
  { key: "details", label: "Film details", hint: "Runtime, genres, countries and release data" },
  { key: "people", label: "Cast & crew", hint: "Credits that link films together" },
  { key: "ratings", label: "Ratings", hint: "IMDb / Rotten Tomatoes (uses the OMDb budget)" },
  { key: "embeddings", label: "Plot embeddings", hint: "Semantic Trope similarity" },
  { key: "facets", label: "Film facets", hint: "Typed facts used by filters and rules" },
];

function percent({ known, total }: CoverageCount): number | null {
  return total > 0 ? Math.round((known / total) * 100) : null;
}

function CoverageBar({ label, count, hint }: { label: string; count: CoverageCount; hint?: string }) {
  const pct = percent(count);
  const text = pct === null ? "no films cached" : `${count.known.toLocaleString()} of ${count.total.toLocaleString()} (${pct}%)`;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="font-medium text-zinc-200">{label}</span>
        <span className="text-xs tabular-nums text-zinc-400">{text}</span>
      </div>
      <div
        role="progressbar"
        aria-label={`${label} coverage`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? 0}
        aria-valuetext={text}
        className="h-2 w-full overflow-hidden rounded-full bg-app-surface-hover"
      >
        <div
          className={pct !== null && pct >= 90 ? "h-full rounded-full bg-emerald-500" : "h-full rounded-full bg-accent"}
          style={{ width: `${pct ?? 0}%` }}
        />
      </div>
      {hint && <p className="text-[11px] text-zinc-500">{hint}</p>}
    </div>
  );
}

function TreatmentButton({ treatment, label, batchCap, primary = false }: {
  treatment: SpaTreatment;
  label: string;
  batchCap: number;
  primary?: boolean;
}) {
  const run = useTrackedTask({ dedupeKey: `spa:${treatment}` });
  const active = run.task && (run.task.status === "pending" || run.task.status === "running") ? run.task : null;
  const paused = run.task ? taskPausedReason(run.task) : null;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <button
        type="button"
        disabled={run.busy}
        onClick={() => void run.start(`/system/spa/${treatment}`, { batch_cap: batchCap })}
        className={
          primary
            ? "flex w-fit items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
            : "flex w-fit items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
        }
      >
        {run.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : primary ? <Wand2 className="h-4 w-4" /> : <Sparkles className="h-3.5 w-3.5 text-accent" />}
        {active ? `${label}: running` : label}
      </button>
      {active && (
        <div className="flex flex-col gap-1" aria-live="polite">
          <TaskProgressBar task={active} />
          <p className="text-[11px] text-zinc-500">{TASK_TITLES[active.name] ?? active.name} · {describeProgress(active)}</p>
        </div>
      )}
      {paused && !active && (
        <p role="status" className="text-xs text-amber-300">{label} paused: {paused}. Run it again after the budget resets to resume.</p>
      )}
      {run.error && <p role="alert" className="text-xs text-red-300">{label}: {run.error}</p>}
    </div>
  );
}

export default function DataSpaPage() {
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  const { data: health, isLoading, isError, error, refetch } = useCacheHealth();
  const [batchInput, setBatchInput] = useState(String(DEFAULT_BATCH_CAP));
  const parsed = Number.parseInt(batchInput, 10);
  const batchValid = Number.isInteger(parsed) && parsed >= 1 && parsed <= MAX_BATCH_CAP;
  const batchCap = batchValid ? parsed : DEFAULT_BATCH_CAP;
  const families = Object.entries(health?.families ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const queryClient = useQueryClient();
  const [softCapInput, setSoftCapInput] = useState<string | null>(null);
  const savedCap = health?.budgets.find((budget) => budget.provider === "omdb")?.limit ?? 0;
  const capInput = softCapInput ?? String(savedCap);
  const softCap = capInput.trim() === "" ? 0 : Number(capInput);
  const capValid = Number.isSafeInteger(softCap) && softCap >= 0;
  const saveCap = useMutation({
    mutationFn: () => api.patch<IntegrationConfig>("/settings/integrations", { omdb_soft_cap: softCap }),
    onSuccess: async () => {
      await Promise.all([
        refetch(),
        queryClient.invalidateQueries({ queryKey: ["settings", "integrations"] }),
      ]);
      setSoftCapInput(null);
    },
  });

  return (
    <div className="flex flex-col gap-5">
      <SettingsCard title="Data Spa · cache health">
        <div className="flex flex-col gap-4 px-5 py-4">
          {isLoading && <p className="text-sm text-zinc-500">Loading cache health...</p>}
          {isError && (
            <p role="alert" className="text-sm text-red-300">
              {error instanceof Error ? error.message : "Couldn't load cache health."}{" "}
              <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
            </p>
          )}
          {health && health.total_movies === 0 && (
            <EmptyState icon={Database} title="The cache is empty" description="Films are cached as you play; treatments repair what is already cached." />
          )}
          {health && (
            <>
              <p className="text-xs text-zinc-500">{health.total_movies.toLocaleString()} cached films</p>
              {TREATMENTS.map(({ key, label, hint }) => (
                <CoverageBar key={key} label={label} hint={hint} count={health.coverage[key] ?? { known: 0, total: health.total_movies }} />
              ))}
            </>
          )}
        </div>
      </SettingsCard>

      {health && families.length > 0 && (
        <SettingsCard title="Facet families">
          <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
            {families.map(([family, count]) => (
              <CoverageBar key={family} label={family.replace(/_/g, " ")} count={count} />
            ))}
          </div>
        </SettingsCard>
      )}

      {health && (
        <SettingsCard title="Provider budgets (today)">
          {isAdmin && (
            <div className="flex flex-col gap-2 px-5 py-4">
              <label htmlFor="omdb-soft-cap" className="text-sm font-medium text-zinc-200">OMDb Soft Cap</label>
              <div className="flex flex-wrap gap-2">
                <input id="omdb-soft-cap" type="number" min={0} step={1}
                  value={capInput} onChange={(event) => setSoftCapInput(event.target.value)}
                  disabled={saveCap.isPending} className={inputClass} />
                <button type="button" disabled={!capValid || saveCap.isPending}
                  onClick={() => saveCap.mutate()}
                  className="rounded-md border border-app-border px-3 py-1.5 text-sm text-zinc-200 disabled:opacity-50">
                  {saveCap.isPending ? "Saving..." : "Save"}
                </button>
              </div>
              <p className="text-xs text-zinc-500">
                {capValid && softCap === 0 ? "Auto (Scales until API limit)" : "Optional maximum daily calls. Zero or blank uses Auto."}
              </p>
              {!capValid && <p role="alert" className="text-xs text-red-300">Enter a non-negative whole number.</p>}
              {saveCap.isError && <p role="alert" className="text-xs text-red-300">{saveCap.error.message}</p>}
              {saveCap.isSuccess && <p role="status" className="text-xs text-emerald-300">Soft cap saved.</p>}
            </div>
          )}
          {health.budgets.length === 0 ? (
            <p className="px-5 py-4 text-sm text-zinc-500">No metered provider calls recorded today.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Daily provider call budgets</caption>
              <thead className="text-xs text-zinc-500">
                <tr>
                  <th scope="col" className="px-5 py-2 font-medium">Provider</th>
                  <th scope="col" className="px-2 py-2 font-medium">Day</th>
                  <th scope="col" className="px-2 py-2 text-right font-medium">Used</th>
                  <th scope="col" className="px-5 py-2 text-right font-medium">Remaining</th>
                </tr>
              </thead>
              <tbody>
                {health.budgets.map((budget) => (
                  <tr key={`${budget.provider}:${budget.day}`} className="border-t border-app-border">
                    <th scope="row" className="px-5 py-2 font-medium text-zinc-200">{budget.provider}</th>
                    <td className="px-2 py-2 text-zinc-400">{budget.day}</td>
                    <td className="px-2 py-2 text-right tabular-nums text-zinc-300">{budget.used} / {budget.limit > 0 ? budget.limit : "Auto (Scales until API limit)"}</td>
                    <td className={budget.exhausted || budget.remaining === 0 ? "px-5 py-2 text-right tabular-nums text-red-300" : "px-5 py-2 text-right tabular-nums text-zinc-300"}>
                      {budget.exhausted ? <span role="status" className="rounded bg-red-950 px-2 py-1 text-xs">API Limit Reached for Today</span> : budget.remaining === 0 ? "Soft cap reached" : budget.remaining ?? "Auto"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </SettingsCard>
      )}

      {isAdmin ? (
        <SettingsCard title="Treatments">
          <div className="flex flex-col gap-4 px-5 py-4">
            <p className="text-xs text-zinc-500">
              Each treatment runs as a background task (see the task indicator), repairs up to the batch cap per run,
              prioritises active runs, and stops cleanly when a provider budget runs out.
            </p>
            <label className="flex w-fit flex-col gap-1 text-xs text-zinc-400">
              Batch cap (1–{MAX_BATCH_CAP})
              <input
                type="number"
                min={1}
                max={MAX_BATCH_CAP}
                value={batchInput}
                onChange={(event) => setBatchInput(event.target.value)}
                aria-invalid={!batchValid}
                className={`${inputClass} w-32`}
              />
              {!batchValid && <span role="alert" className="text-red-300">Using {DEFAULT_BATCH_CAP}: enter 1–{MAX_BATCH_CAP}.</span>}
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              {TREATMENTS.map(({ key, label }) => (
                <TreatmentButton key={key} treatment={key} label={label} batchCap={batchCap} />
              ))}
            </div>
            <div className="border-t border-app-border pt-4">
              <TreatmentButton treatment="fix_all" label="Fix all" batchCap={batchCap} primary />
            </div>
          </div>
        </SettingsCard>
      ) : (
        <p className="rounded-xl border border-dashed border-app-border px-5 py-6 text-center text-sm text-zinc-500">
          Only administrators can run Data Spa treatments. You can prepare a single run from its Pick Next screen.
        </p>
      )}
    </div>
  );
}
