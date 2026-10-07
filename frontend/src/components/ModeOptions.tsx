import { cn } from "../lib/cn";
import { defaultModifierParams, modifierEnabled, modifierParams, modifierWarnings, setModifier } from "../lib/modifiers";
import type { EngineMeta, ModifierMeta, ModifierParamSchema, ModifierParamValue, RulesConfig } from "../types/api";

const OPTION_LABELS: Record<string, string> = {
  asc: "Increasing",
  desc: "Decreasing",
  az: "A to Z",
  za: "Z to A",
  last_first: "Last letter to first",
  first_last: "First letter to last",
};

export default function ModeOptions({
  gameType, value, onChange, engine, editing = false,
}: {
  gameType: string;
  value: RulesConfig;
  onChange: (rules: RulesConfig) => void;
  engine?: EngineMeta;
  editing?: boolean;
}) {
  if (!engine) return <p role="status" className="text-xs text-zinc-400">Loading modifiers...</p>;
  if (!engine.modifiers.some((spec) => spec.compatible)) return null;
  function paramControl(spec: ModifierMeta, key: string, schema: ModifierParamSchema) {
    const params = { ...defaultModifierParams(spec), ...modifierParams(spec, value) };
    const current = params[key];
    const id = `modifier-${gameType}-${spec.key}-${key}`;
    const label = schema.title ?? key.replaceAll("_", " ");
    const change = (next: ModifierParamValue) => onChange(setModifier(value, spec, { ...params, [key]: next }));
    const disabled = editing && spec.scope === "film" && spec.key === "number_in_title"
      && ["method_actor", "auteur_marathon", "regional_deep_dive"].includes(gameType);
    return (
      <label key={key} htmlFor={id} className="flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-300">
        {label}
        {schema.type === "boolean" ? (
          <input id={id} type="checkbox" checked={current === true} disabled={disabled}
            onChange={(event) => change(event.target.checked)} className="accent-accent" />
        ) : schema.enum ? (
          <select id={id} value={typeof current === "string" ? current : ""} disabled={disabled}
            onChange={(event) => change(event.target.value)}
            className="max-w-full rounded border border-app-border bg-app-bg px-2 py-1 capitalize">
            {schema.enum.map((option) => <option key={option} value={option}>{OPTION_LABELS[option] ?? option.replaceAll("_", " ")}</option>)}
          </select>
        ) : schema.type === "integer" ? (
          <input id={id} type="number" min={schema.minimum} max={schema.maximum} disabled={disabled}
            value={typeof current === "number" ? current : ""}
            onChange={(event) => change(Number(event.target.value))}
            className="w-24 rounded border border-app-border bg-app-bg px-2 py-1 text-right" />
        ) : schema.type === "array" ? (
          <span className="flex gap-2">
            {(schema.items?.enum ?? []).map((option) => (
              <label key={option} className="flex gap-1">
                <input type="checkbox" checked={Array.isArray(current) && current.includes(option)}
                  onChange={(event) => change(event.target.checked
                    ? [...(Array.isArray(current) ? current : []), option]
                    : (Array.isArray(current) ? current : []).filter((item) => item !== option))}
                  className="accent-accent" />{option}
              </label>
            ))}
          </span>
        ) : null}
      </label>
    );
  }
  return (
    <section aria-label="Composable modifiers" className="flex flex-col gap-3 rounded-lg border border-app-border bg-app-bg/60 p-3">
      {gameType === "historical_time_travel" && (
        <label className="flex items-center justify-between gap-2 text-xs text-zinc-300">
          Direction through history
          <select aria-label="Direction through history" value={value.direction ?? "climb"}
            onChange={(event) => onChange({ ...value, direction: event.target.value === "descent" ? "descent" : "climb" })}
            className="rounded border border-app-border bg-app-bg px-2 py-1">
            <option value="climb">Forward</option><option value="descent">Backward</option>
          </select>
        </label>
      )}
      {engine.modifiers.map((spec) => {
        const enabled = modifierEnabled(modifierParams(spec, value));
        const fixed = editing && spec.key === "number_in_title"
          && ["method_actor", "auteur_marathon", "regional_deep_dive"].includes(gameType);
        const reason = spec.incompatible_reason ?? (fixed ? "Filtered checklist is chosen at creation." : null);
        return (
          <div key={spec.key} className={cn("flex flex-col gap-2", !spec.compatible && "opacity-50")}>
            <label className="flex items-start justify-between gap-3 text-xs text-zinc-200">
              <span className="min-w-0"><span className="font-medium">{spec.emoji} {spec.label}</span>
                <span className="mt-0.5 block text-[11px] text-zinc-500">{reason ?? spec.blurb}</span>
              </span>
              <input type="checkbox" aria-label={spec.label} checked={enabled}
                disabled={!spec.compatible || fixed || (spec.key === "chrono_direction" && !!spec.default_params)}
                onChange={() => onChange(setModifier(value, spec, enabled ? null : defaultModifierParams(spec)))}
                className="mt-0.5 shrink-0 accent-accent" />
            </label>
            {enabled && spec.compatible && (
              <div className="ml-3 flex flex-col gap-2 border-l border-app-border pl-3">
                {Object.entries(spec.params_schema.properties).map(([key, schema]) => paramControl(spec, key, schema))}
              </div>
            )}
          </div>
        );
      })}
      {modifierWarnings(gameType, value).map((warning) => (
        <p key={warning.headline} role="alert" className="rounded border border-amber-800/50 p-2 text-[11px] text-amber-300">
          {warning.headline} {warning.why}
        </p>
      ))}
    </section>
  );
}
