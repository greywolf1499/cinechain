import { useEffect, useState, type ReactNode } from "react";
import { Award, BookUser, Loader2 } from "lucide-react";
import EmptyState from "./EmptyState";
import { isoToFlagEmoji } from "../lib/countries";
import { useRunStats, useRuns } from "../lib/queries";

const MEDALS = ["🥇", "🥈", "🥉"];

/** Per-run cultural-breadth stats (hops, keystone actors) - the original Passport view. */
export default function RunStatsSection() {
  const { data: runs, isLoading: runsLoading } = useRuns();
  const [selectedRunId, setSelectedRunId] = useState("");

  useEffect(() => {
    if (!selectedRunId && runs && runs.length > 0) {
      setSelectedRunId(runs[0].id);
    }
  }, [runs, selectedRunId]);

  const { data: stats, isLoading } = useRunStats(selectedRunId || undefined);

  return (
    <div>
      {runs && runs.length > 0 && (
        <select
          value={selectedRunId}
          onChange={(e) => setSelectedRunId(e.target.value)}
          className="mb-4 rounded-md border border-app-border bg-app-surface px-3 py-2 text-sm text-zinc-200 focus:border-accent focus:outline-none"
        >
          {runs.map((run) => (
            <option key={run.id} value={run.id}>
              {run.name}
            </option>
          ))}
        </select>
      )}

      {runsLoading || (runs && runs.length > 0 && (isLoading || !stats)) ? (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-zinc-600" />
        </div>
      ) : !runs || runs.length === 0 ? (
        <EmptyState
          icon={BookUser}
          title="No runs yet"
          description="Start a run and log a few films to build up your passport stats."
        />
      ) : stats ? (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <StatCard label="Hops Traveled" value={stats.total_hops} />
            <StatCard label="Countries Visited" value={stats.countries.length} />
            <StatCard label="Decades Spanned" value={stats.decades.length} />
          </div>

          <Section title="Origin Countries">
            {stats.countries.length === 0 ? (
              <p className="text-sm text-zinc-500">No watched films yet.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {stats.countries.map((country) => (
                  <span
                    key={country}
                    className="flex items-center gap-1.5 rounded-full border border-app-border bg-app-surface px-3 py-1.5 text-sm text-zinc-200"
                  >
                    <span>{isoToFlagEmoji(country)}</span>
                    {country}
                  </span>
                ))}
              </div>
            )}
          </Section>

          <Section title="Decades Traversed">
            {stats.decades.length === 0 ? (
              <p className="text-sm text-zinc-500">No watched films yet.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {stats.decades.map((decade) => (
                  <span
                    key={decade}
                    className="rounded-full bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent"
                  >
                    {decade}s
                  </span>
                ))}
              </div>
            )}
          </Section>

          <Section title="Keystone Actors">
            {stats.keystone_actors.length === 0 ? (
              <p className="text-sm text-zinc-500">No connecting actors recorded yet.</p>
            ) : (
              <div className="flex flex-col divide-y divide-app-border rounded-lg border border-app-border bg-app-surface">
                {stats.keystone_actors.map((actor, index) => (
                  <div key={actor.actor_id} className="flex items-center justify-between px-4 py-2.5">
                    <div className="flex items-center gap-2.5">
                      <span className="flex w-5 justify-center text-sm">
                        {MEDALS[index] ?? <Award className="h-3.5 w-3.5 text-zinc-600" />}
                      </span>
                      <span className="text-sm text-zinc-200">{actor.actor_name}</span>
                    </div>
                    <span className="text-xs text-zinc-500">{actor.appearances}x</span>
                  </div>
                ))}
              </div>
            )}
          </Section>
        </div>
      ) : null}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <p className="text-2xl font-semibold text-zinc-100">{value}</p>
      <p className="mt-1 text-xs text-zinc-500">{label}</p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h2 className="mb-2.5 text-sm font-medium text-zinc-300">{title}</h2>
      {children}
    </div>
  );
}
