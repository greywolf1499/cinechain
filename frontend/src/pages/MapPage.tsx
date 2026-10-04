import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, Loader2, Map as MapIcon } from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";
import StatusBadge from "../components/StatusBadge";
import WorldMap, { type RouteStop } from "../components/WorldMap";
import { isoToFlagEmoji, parseOriginCountries } from "../lib/countries";
import { countryName } from "../lib/countryNames";
import { useRun, useRuns } from "../lib/queries";
import { useActiveRunStore } from "../store/activeRunStore";
import type { PassportCountry, RunStep } from "../types/api";

/** The country a film is credited to for the route: the first (primary) origin country. */
function stepCountry(step: RunStep): string | null {
  return parseOriginCountries(step.movie_origin_country)[0]?.toUpperCase() ?? null;
}

/** Run Map: pick a run and see its marathon plotted across the world, step by step. */
export default function MapPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeRunId = useActiveRunStore((s) => s.activeRunId);
  const { data: runs, isLoading: runsLoading } = useRuns();

  const requested = searchParams.get("run_id") ?? activeRunId;
  const selectedId =
    (runs?.some((run) => run.id === requested) ? requested : runs?.[0]?.id) ?? undefined;
  const { data: run, isLoading: runLoading } = useRun(selectedId);

  const { route, countries, unknownSteps } = useMemo(() => {
    const steps = run?.steps ?? [];
    const stops: RouteStop[] = [];
    const counts = new Map<string, number>();
    let unknown = 0;
    steps.forEach((step, index) => {
      const code = stepCountry(step);
      if (!code) {
        unknown += 1;
        return;
      }
      stops.push({ step: index + 1, code });
      counts.set(code, (counts.get(code) ?? 0) + 1);
    });
    const list: PassportCountry[] = [...counts].map(([code, count]) => ({ code, count, merged_from: [] }));
    return { route: stops, countries: list, unknownSteps: unknown };
  }, [run]);

  return (
    <div>
      <Link
        to="/tools"
        className="mb-3 inline-flex items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Tools
      </Link>
      <PageHeading title="Run Map" subtitle="Trace a run's marathon across the world, film by film." />

      {runsLoading && (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-zinc-600" />
        </div>
      )}

      {!runsLoading && (runs?.length ?? 0) === 0 && (
        <EmptyState
          icon={MapIcon}
          title="No runs to map yet"
          description="Start a run and log a few films - their countries will light up here."
        />
      )}

      {runs && runs.length > 0 && (
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs font-medium text-zinc-400">
              Run
              <select
                value={selectedId ?? ""}
                onChange={(e) => setSearchParams({ run_id: e.target.value }, { replace: true })}
                className="min-w-56 rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 focus:border-accent focus:outline-none"
              >
                {runs.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.name} ({candidate.status})
                  </option>
                ))}
              </select>
            </label>
            {run && <StatusBadge status={run.status} />}
            {run && (
              <span className="text-xs text-zinc-500">
                {countries.length} {countries.length === 1 ? "country" : "countries"} across{" "}
                {run.steps.length} {run.steps.length === 1 ? "film" : "films"}
              </span>
            )}
          </div>

          {runLoading && (
            <div className="flex justify-center py-10">
              <Loader2 className="h-5 w-5 animate-spin text-zinc-600" />
            </div>
          )}

          {run && (
            <>
              <WorldMap countries={countries} route={route} legend={false} />

              {run.steps.length === 0 ? (
                <p className="text-sm text-zinc-500">This run has no films yet.</p>
              ) : (
                <section aria-label="Route timeline">
                  <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">Route</h2>
                  <ol className="flex flex-col gap-1.5">
                    {run.steps.map((step, index) => {
                      const code = stepCountry(step);
                      const previous = index > 0 ? stepCountry(run.steps[index - 1]) : null;
                      return (
                        <li
                          key={step.id}
                          className="flex items-center gap-3 rounded-lg border border-app-border bg-app-surface px-3 py-2"
                        >
                          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-bold text-zinc-950">
                            {index + 1}
                          </span>
                          <span className="w-44 shrink-0 text-sm font-medium text-zinc-100">
                            {code ? (
                              <>
                                {isoToFlagEmoji(code)} {countryName(code, code)}
                              </>
                            ) : (
                              <span className="text-zinc-500">Unknown country</span>
                            )}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-sm text-zinc-400">
                            {step.movie_title}
                            {step.movie_release_year && (
                              <span className="ml-1.5 text-zinc-600">({step.movie_release_year})</span>
                            )}
                          </span>
                          {code && previous && code !== previous && (
                            <span className="hidden shrink-0 text-[11px] text-zinc-600 sm:inline">
                              from {countryName(previous, previous)}
                            </span>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                  {unknownSteps > 0 && (
                    <p className="mt-2 text-xs text-zinc-600">
                      {unknownSteps} {unknownSteps === 1 ? "film has" : "films have"} no country on record and
                      can't be pinned.
                    </p>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
