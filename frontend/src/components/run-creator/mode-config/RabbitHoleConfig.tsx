import { Field, inputClass } from "../shared";
import { useRabbitHolePreview } from "../../../lib/queries";
import type { ModeConfigProps } from "./types";

export default function RabbitHoleConfig({ draft, update }: ModeConfigProps) {
  const fog = draft.rules.fog ?? "off";
  const preview = useRabbitHolePreview(
    draft.rules,
    fog === "off" && draft.rules.preset !== "loading",
  );
  const tiers = preview.data?.tier_deck ?? [];
  const depth = draft.escapeDepth ?? 25;
  const maxDepth = Math.max(25, depth);
  return (
    <div className="rounded-lg border border-red-400/30 bg-red-500/5 p-3">
      <Field label="Fog of war">
        <select
          value={fog}
          onChange={(event) => update({
            rules: { ...draft.rules, fog: event.target.value as "off" | "fog" | "abyss" },
          })}
          className={inputClass}
        >
          <option value="off">Off — see the full deck</option>
          <option value="fog">Fog — hidden rules + one Periscope charge</option>
          <option value="abyss">Abyss — hidden rules, no reveal</option>
        </select>
      </Field>
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
      {fog === "off" && (
        <section className="mt-4 rounded-md border border-app-border bg-app-bg/70 p-3" aria-label="Rabbit Hole deck preview">
          <div className="flex items-center justify-between text-xs">
            <span className="font-semibold text-zinc-200">Estimated descent</span>
            <span className="text-zinc-400">{tiers.length || "—"} tiers · depth {draft.escapeDepth ?? "endless"}</span>
          </div>
          {tiers.length > 0 && (
            <>
              <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-zinc-800" aria-label={`Depth gauge to ${maxDepth}`}>
                {tiers.map((tier, index) => {
                  const end = tiers[index + 1]?.start_depth ?? maxDepth;
                  const segment = Math.max(0, Math.min(maxDepth, end) - Math.min(maxDepth, tier.start_depth));
                  return <span
                    key={tier.number}
                    className="border-r border-app-bg bg-rose-400/70 last:border-0"
                    style={{ width: `${(segment / maxDepth) * 100}%` }}
                    title={`Tier ${tier.number}: ${tier.rule ?? tier.name}`}
                  />;
                })}
              </div>
              <ol className="mt-3 grid gap-1 text-[11px] text-zinc-400 sm:grid-cols-2">
                {tiers.map((tier) => (
                  <li key={tier.number}>
                    {tier.emoji ?? "●"} Depth {tier.start_depth}: {tier.rule ?? tier.name}
                    {tier.difficulty != null && <span className="ml-1 text-amber-300">· difficulty {tier.difficulty}</span>}
                  </li>
                ))}
              </ol>
            </>
          )}
          {preview.isLoading && <p className="mt-2 text-[11px] text-zinc-500">Building a cache-only deck preview…</p>}
          {preview.isError && <p role="status" className="mt-2 text-[11px] text-amber-300">
            Preview unavailable: {preview.error.message}
          </p>}
        </section>
      )}
      {fog !== "off" && <p className="mt-3 text-[11px] text-zinc-500">Deck preview is hidden while Fog is active.</p>}
    </div>
  );
}
