import { Field, inputClass } from "../shared";
import type { ModeConfigProps } from "./types";

export default function RabbitHoleConfig({ draft, update }: ModeConfigProps) {
  return (
    <div className="rounded-lg border border-red-400/30 bg-red-500/5 p-3">
      <Field label="Escape depth (optional)">
        <input
          type="number"
          min={25}
          max={60}
          step={1}
          value={draft.escapeDepth ?? ""}
          onChange={(event) =>
            update({ escapeDepth: event.target.value === "" ? null : Number(event.target.value) })
          }
          placeholder="No escape goal"
          className={inputClass}
        />
      </Field>
      <p className="mt-2 text-[11px] text-zinc-500">
        Reach depth 25–60 to escape and complete the run. Leave blank for endless descent.
      </p>
    </div>
  );
}
