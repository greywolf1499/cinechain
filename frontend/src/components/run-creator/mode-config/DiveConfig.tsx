import { EXPEDITION_COUNTRIES } from "../../../lib/expedition";
import { isoToFlagEmoji } from "../../../lib/countries";
import { DECADES, Field, inputClass } from "../shared";
import { CanonListSelect } from "./CanonListConfig";
import type { ModeConfigProps } from "./types";

export default function DiveConfig(props: ModeConfigProps) {
  const { draft, update } = props;
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-lime-400/30 bg-lime-500/5 p-3">
      <CanonListSelect
        value={draft.diveListId}
        onChange={(diveListId) => update({ diveListId })}
        curatedLists={props.curatedLists}
        onGoLists={props.onGoLists}
        label="Canon list to slice"
      />
      <div data-blocker-target="dive-slice" className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Country">
          <select value={draft.diveCountry} onChange={(event) => update({ diveCountry: event.target.value })} className={inputClass}>
            <option value="">Any country</option>
            {EXPEDITION_COUNTRIES.map((country) => (
              <option key={country.code} value={country.code}>{isoToFlagEmoji(country.code)} {country.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Decade (optional)">
          <select value={draft.diveDecade} onChange={(event) => update({ diveDecade: event.target.value })} className={inputClass}>
            <option value="">Any decade</option>
            {DECADES.map((decade) => <option key={decade} value={decade}>{decade}s</option>)}
          </select>
        </Field>
      </div>
      {!draft.diveCountry && !draft.diveDecade && (
        <span className="text-[11px] text-zinc-500">Pick a country, a decade or both to slice the list.</span>
      )}
    </div>
  );
}
