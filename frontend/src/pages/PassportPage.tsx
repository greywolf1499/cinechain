import { lazy, Suspense, type ReactNode } from "react";
import { BookUser, Clapperboard, Loader2 } from "lucide-react";
import DecadesChart from "../components/DecadesChart";
import EmptyState from "../components/EmptyState";
import ImportHistory from "../components/ImportHistory";
import PageHeading from "../components/PageHeading";
import RunStatsSection from "../components/RunStatsSection";
import CountryFlags from "../components/CountryFlags";
import { usePassport } from "../lib/queries";
import type { Passport } from "../types/api";

// The world-map path data is ~100KB gzipped, so it loads only when the Passport opens.
const WorldMap = lazy(() => import("../components/WorldMap"));

export default function PassportPage() {
  const { data: passport, isLoading, error } = usePassport();

  return (
    <div>
      <PageHeading
        title="Passport"
        subtitle="Everything you've watched across every run and your imported Letterboxd history"
      />

      {isLoading && (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-zinc-600" />
        </div>
      )}
      {error && <p className="text-sm text-red-400">Could not load your passport.</p>}

      {passport && (
        <div className="flex flex-col gap-8">
          {passport.total_watches === 0 && (
            <EmptyState
              icon={BookUser}
              title="Your passport is empty"
              description="Log films in a run, or import your Letterboxd diary below, to start filling in the map."
            />
          )}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Films watched" value={passport.total_movies_watched} />
            <StatCard label="Total watches" value={passport.total_watches} hint="rewatches included" />
            <StatCard label="Countries visited" value={passport.countries.length} />
            <StatCard label="Decades spanned" value={Object.keys(passport.decades_distribution).length} />
          </div>

          <Section title="Global Cinema Scratch-Off">
            <Suspense fallback={<div className="flex h-64 items-center justify-center rounded-xl border border-app-border bg-app-bg"><Loader2 className="h-5 w-5 animate-spin text-zinc-600" /></div>}>
              <WorldMap countries={passport.countries} />
            </Suspense>
            <CountryList countries={passport.countries} />
          </Section>

          <div className="grid grid-cols-1 gap-8 xl:grid-cols-5">
            <div className="xl:col-span-3">
              <Section title="Films by Decade">
                <DecadesChart distribution={passport.decades_distribution} />
              </Section>
            </div>
            <div className="xl:col-span-2">
              <Section title="Top Directors">
                <TopDirectors passport={passport} />
              </Section>
            </div>
          </div>

          <Section title="Import History">
            <ImportHistory coverage={passport.directors_coverage} />
          </Section>

          <details className="group rounded-xl border border-app-border bg-app-surface">
            <summary className="cursor-pointer px-5 py-3 text-sm font-medium text-zinc-300">Per-run stats</summary>
            <div className="border-t border-app-border px-5 py-4">
              <RunStatsSection />
            </div>
          </details>
        </div>
      )}
    </div>
  );
}

function CountryList({ countries }: { countries: Passport["countries"] }) {
  if (countries.length === 0) return null;
  return (
    <ul className="mt-4 flex flex-wrap gap-2" aria-label="Visited countries">
      {countries.map((country) => (
        <li
          key={country.code}
          title={country.merged_from.length ? `Includes ${country.merged_from.join(", ")} (historical)` : undefined}
          className="flex items-center gap-1.5 rounded-full border border-app-border bg-app-surface px-3 py-1 text-xs text-zinc-300"
        >
          <CountryFlags codes={[country.code]} />
          <span className="font-medium text-accent">{country.count}</span>
        </li>
      ))}
    </ul>
  );
}

function TopDirectors({ passport }: { passport: Passport }) {
  const { top_directors: directors, directors_coverage: coverage } = passport;
  if (directors.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-app-border px-4 py-8 text-center text-sm text-zinc-500">
        {coverage.movies_total > 0
          ? "Director data hasn't been looked up yet - use the button under Import History."
          : "No watched films yet."}
      </p>
    );
  }
  const max = directors[0].count;
  return (
    <ol className="flex flex-col divide-y divide-app-border rounded-xl border border-app-border bg-app-surface">
      {directors.map((director, index) => (
        <li key={director.person_id} className="flex flex-col gap-1.5 px-4 py-3">
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="flex min-w-0 items-center gap-2.5 text-zinc-200">
              <span className="w-4 shrink-0 text-center text-xs text-zinc-600">{index + 1}</span>
              <Clapperboard className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
              <span className="truncate">{director.name}</span>
            </span>
            <span className="shrink-0 text-xs text-zinc-500">
              {director.count} {director.count === 1 ? "film" : "films"}
            </span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-app-surface-hover">
            <div className="h-full rounded-full bg-accent" style={{ width: `${(director.count / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ol>
  );
}

function StatCard({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <p className="text-2xl font-semibold text-zinc-100">{value.toLocaleString()}</p>
      <p className="mt-1 text-xs text-zinc-500">
        {label}
        {hint && <span className="text-zinc-600"> &middot; {hint}</span>}
      </p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-400">{title}</h2>
      {children}
    </section>
  );
}
