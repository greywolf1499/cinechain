import { Field, inputClass } from "../shared";
import type { ModeConfigProps } from "./types";

export default function SplitConfig({ draft, update }: ModeConfigProps) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-pink-400/30 bg-pink-500/5 p-3">
      <p className="text-xs text-zinc-400">
        🍅 The run owner is Team Critic, the first partner Team Audience 🍿. Only films with a
        Rotten Tomatoes score and IMDb rating at least 25 points apart count; the household rating
        decides which side wins the point.
      </p>
      <Field label="First to (points)">
        <input
          type="number"
          min={1}
          max={20}
          value={draft.splitTarget}
          onChange={(event) => update({ splitTarget: Math.min(20, Math.max(1, Number(event.target.value) || 1)) })}
          className={inputClass}
        />
      </Field>
    </div>
  );
}
