import type { DiscoveryCandidate, FilterSpec, RunStep } from "../../types/api";
import { isoToFlagEmoji, parseOriginCountries } from "../../lib/countries";
import { countryName } from "../../lib/countryNames";

export type FilterValue = boolean | string | [number | null, number | null];
export type FilterValues = Record<string, FilterValue>;

export function candidateCountries(candidate: DiscoveryCandidate): string[] {
  return candidate.origin_countries ?? parseOriginCountries(candidate.origin_country);
}

export function visitedCountries(steps: RunStep[]): Set<string> {
  return new Set(steps.flatMap((step) => step.movie_origin_countries ?? parseOriginCountries(step.movie_origin_country)));
}

export function filterDefaults(specs: FilterSpec[], visited: Set<string>): FilterValues {
  return Object.fromEntries(specs.map((spec) => [
    spec.key,
    spec.default === "after_three_stamps"
      ? visited.size >= 3
      : spec.kind === "range" ? [null, null] : typeof spec.default === "number" ? String(spec.default) : spec.default ?? (spec.kind === "toggle" ? false : ""),
  ]));
}

export function filterDataUnknown(candidate: DiscoveryCandidate, spec: FilterSpec): boolean {
  switch (spec.source) {
    case "origin_country":
    case "new_country": return candidateCountries(candidate).length === 0;
    case "genre_ids": return candidate.genre_ids.length === 0;
    case "tug_effect": return candidate.tug_effect == null;
    case "tier_compliant": return candidate.tier_compliant == null;
    case "release_year": return candidate.release_year == null;
    case "narrative_year": return candidate.narrative_year == null;
    case "runtime": return candidate.runtime == null || candidate.runtime <= 0;
  }
}

export function matchesModeFilters(
  candidate: DiscoveryCandidate,
  specs: FilterSpec[],
  values: FilterValues,
  visited: Set<string>,
  targetGenreId: number | undefined,
): boolean {
  return specs.every((spec) => {
    const value = values[spec.key];
    if (spec.server_param || value == null || value === false || value === "" || filterDataUnknown(candidate, spec)) return true;
    switch (spec.source) {
      case "origin_country": return typeof value !== "string" || candidateCountries(candidate).includes(value);
      case "new_country": return candidateCountries(candidate).some((code) => !visited.has(code));
      case "genre_ids": return targetGenreId == null || candidate.genre_ids.includes(targetGenreId);
      case "tug_effect": return value === "neutral"
        ? candidate.tug_effect === "neutral" || candidate.tug_effect === "sudden_neutral"
        : candidate.tug_effect === value;
      case "tier_compliant": return candidate.tier_compliant !== false;
      case "release_year":
      case "narrative_year":
      case "runtime": {
        if (!Array.isArray(value)) return true;
        const number = candidate[spec.source];
        return number == null || ((value[0] == null || number >= value[0]) && (value[1] == null || number <= value[1]));
      }
    }
  });
}

export default function ModeFilterBar({
  specs, pool, values, onChange, cooldown, frontierYear, frontierNarrativeYear, descending,
}: {
  specs: FilterSpec[];
  pool: DiscoveryCandidate[];
  values: FilterValues;
  onChange: (key: string, value: FilterValue) => void;
  cooldown: Map<string, number>;
  frontierYear: number | null;
  frontierNarrativeYear: number | null;
  descending: boolean;
}) {
  if (!specs.length) return null;
  return (
    <div className="flex flex-wrap items-end gap-3 rounded-lg border border-app-border p-3" aria-label="Mode filters">
      {specs.map((spec) => {
        const value = values[spec.key];
        const unknown = pool.filter((candidate) => filterDataUnknown(candidate, spec)).length;
        let control;
        if (spec.kind === "toggle") {
          control = <label className="flex items-center gap-2 text-sm text-zinc-200">
            <input type="checkbox" checked={value === true} onChange={(e) => onChange(spec.key, e.target.checked)} className="accent-accent" />
            {spec.label}
          </label>;
        } else if (spec.kind === "select") {
          const counts = new Map<string, number>();
          for (const candidate of pool) {
            const options = spec.source === "origin_country" ? candidateCountries(candidate)
              : spec.source === "tug_effect" && candidate.tug_effect ? [candidate.tug_effect === "sudden_neutral" ? "neutral" : candidate.tug_effect] : [];
            for (const option of options) counts.set(option, (counts.get(option) ?? 0) + 1);
          }
          const optionLabel = (key: string) => spec.source === "origin_country"
            ? `${isoToFlagEmoji(key)} ${countryName(key, key)}`
            : ({ home: "Build", invasion: "Raid", neutral: "Bank" }[key] ?? key);
          const options = [...counts].sort(([a], [b]) => optionLabel(a).localeCompare(optionLabel(b)));
          // Keep a selected option visible if a new server pool no longer contains it.
          if (typeof value === "string" && value && !counts.has(value)) options.push([value, 0]);
          control = <label className="flex flex-col gap-1 text-xs text-zinc-400">
            {spec.label}
            <select value={typeof value === "string" ? value : ""} onChange={(e) => onChange(spec.key, e.target.value)} className="rounded-md border border-app-border bg-app-bg px-2.5 py-2 text-sm text-zinc-200">
              <option value="">All {spec.label.toLowerCase() === "country" ? "countries" : "effects"}</option>
              {options.map(([key, count]) => <option key={key} value={key} disabled={spec.source === "origin_country" && cooldown.has(key)}>
                {optionLabel(key)} ({count}){spec.source === "origin_country" && cooldown.has(key) ? ` - Cooling down (${cooldown.get(key)} more)` : ""}
              </option>)}
            </select>
          </label>;
        } else {
          const range = Array.isArray(value) ? value : [null, null];
          const numbers = pool.map((candidate) => spec.source === "release_year" ? candidate.release_year
            : spec.source === "narrative_year" ? candidate.narrative_year : candidate.runtime).filter((number): number is number => number != null);
          const frontier = spec.source === "release_year" ? frontierYear : spec.source === "narrative_year" ? frontierNarrativeYear : null;
          const min = frontier != null && !descending ? frontier + 1 : numbers.length ? Math.min(...numbers) : undefined;
          const max = frontier != null && descending ? frontier - 1 : numbers.length ? Math.max(...numbers) : undefined;
          control = <fieldset className="text-xs text-zinc-400">
            <legend className="mb-1">{spec.label}</legend>
            <div className="flex items-center gap-1">
              {(["From", "To"] as const).map((label, index) => <input key={label} type="number" aria-label={`${spec.label} ${label.toLowerCase()}`}
                placeholder={String((index === 0 ? min : max) ?? label)} min={min} max={max} value={range[index] ?? ""}
                onChange={(e) => onChange(spec.key, index === 0 ? [e.target.value === "" ? null : e.target.valueAsNumber, range[1]] : [range[0], e.target.value === "" ? null : e.target.valueAsNumber])}
                className="w-24 rounded-md border border-app-border bg-app-bg px-2 py-2 text-zinc-200" />)}
            </div>
            {range[0] != null && range[1] != null && range[0] > range[1] && <p role="alert" className="mt-1 text-red-300">From must not be after To.</p>}
          </fieldset>;
        }
        return <div key={spec.key} title={spec.help} className="flex flex-col gap-1">
          {control}
          {unknown > 0 && <span className="text-[10px] text-amber-300" title="Missing data stays in the pool, even with this filter active.">? {unknown} unverified - kept visible</span>}
        </div>;
      })}
      {[...cooldown].map(([code, remaining]) => <span key={code} aria-disabled="true" className="rounded-full bg-app-surface-hover px-2 py-1 text-[10px] text-zinc-500">
        {isoToFlagEmoji(code)} {countryName(code, code)} · Cooling down ({remaining} more)
      </span>)}
    </div>
  );
}
