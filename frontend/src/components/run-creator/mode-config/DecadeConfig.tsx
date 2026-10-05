import { DECADES, Field, inputClass } from "../shared";
import type { ModeConfigProps } from "./types";

export default function DecadeConfig({ draft, update }: ModeConfigProps) {
  return (
    <Field label="Decade (every film must be released in it)">
      <select
        value={draft.targetDecade}
        onChange={(event) => update({ targetDecade: Number(event.target.value) })}
        className={inputClass}
      >
        {DECADES.map((decade) => <option key={decade} value={decade}>{decade}s</option>)}
      </select>
    </Field>
  );
}
