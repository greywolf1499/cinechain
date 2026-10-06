import { EXPEDITION_COUNTRIES } from "../../../lib/expedition";
import { isoToFlagEmoji } from "../../../lib/countries";
import { DECADES, Field, inputClass } from "../shared";
import { CanonListSelect } from "./CanonListConfig";
import type { ModeConfigProps } from "./types";
import { useCuratedSlices } from "../../../lib/queries";
import { countryName } from "../../../lib/countryNames";

export default function DiveConfig(props: ModeConfigProps) {
  const { draft, update } = props;
  const slices = useCuratedSlices(draft.diveListId);
  const counts = slices.data;
  const codes = [...new Set([
    ...EXPEDITION_COUNTRIES.map((country) => country.code),
    ...Object.keys(counts?.countries ?? {}),
  ])].sort((a, b) => countryName(a).localeCompare(countryName(b)));
  const decades = [...new Set([...DECADES, ...Object.keys(counts?.decades ?? {}).map(Number)])].sort((a, b) => b - a);

  function surprise() {
    const pairs = Object.entries(counts?.pairs ?? {}).filter(([, count]) => count > 0);
    const weighted = pairs.map(([pair, count]) => ({ pair, weight: count >= 5 && count <= 25 ? 4 : 1 }));
    let draw = Math.random() * weighted.reduce((sum, item) => sum + item.weight, 0);
    for (const item of weighted) {
      draw -= item.weight;
      if (draw < 0) {
        const [diveCountry, diveDecade] = item.pair.split(":");
        update({ diveCountry, diveDecade });
        return;
      }
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-lime-400/30 bg-lime-500/5 p-3">
      <CanonListSelect
        value={draft.diveListId}
        onChange={(diveListId) => update({ diveListId, diveCountry: "", diveDecade: "" })}
        curatedLists={props.curatedLists}
        onGoLists={props.onGoLists}
        label="Canon list to slice"
      />
      <div data-blocker-target="dive-slice" className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Country">
          <select disabled={!counts || slices.isError} value={draft.diveCountry} onChange={(event) => update({ diveCountry: event.target.value })} className={inputClass}>
            <option value="">Any country</option>
            {codes.map((code) => {
              const count = (draft.diveDecade ? counts?.pairs[`${code}:${draft.diveDecade}`] : counts?.countries[code]) ?? 0;
              return <option key={code} value={code} disabled={count === 0}>{isoToFlagEmoji(code)} {countryName(code, code)} · {count}</option>;
            })}
          </select>
        </Field>
        <Field label="Decade (optional)">
          <select disabled={!counts || slices.isError} value={draft.diveDecade} onChange={(event) => update({ diveDecade: event.target.value })} className={inputClass}>
            <option value="">Any decade</option>
            {decades.map((decade) => {
              const count = (draft.diveCountry ? counts?.pairs[`${draft.diveCountry}:${decade}`] : counts?.decades[String(decade)]) ?? 0;
              return <option key={decade} value={decade} disabled={count === 0}>{decade}s · {count}</option>;
            })}
          </select>
        </Field>
      </div>
      <button type="button" onClick={surprise} disabled={!counts || !Object.keys(counts.pairs).length || slices.isError} className="rounded-md border border-lime-400/30 p-2 text-xs text-lime-200 disabled:opacity-50">
        🎲 Surprise me
      </button>
      {counts?.indexing && <p role="status" className="text-xs text-zinc-400">Indexing {counts.hydrated}/{counts.total} films...</p>}
      {counts && !counts.indexing && counts.hydrated < counts.total && <p className="text-xs text-amber-400">Only {counts.hydrated}/{counts.total} films are indexed. Sync the list to retry missing details.</p>}
      {slices.isError && <p role="alert" className="text-xs text-amber-400">{slices.error.message}</p>}
      {counts?.indexing_error && <p role="alert" className="text-xs text-amber-400">{counts.indexing_error}</p>}
      {!draft.diveCountry && !draft.diveDecade && (
        <span className="text-[11px] text-zinc-500">Pick a country, a decade or both to slice the list.</span>
      )}
    </div>
  );
}
