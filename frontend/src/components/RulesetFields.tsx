import { cn } from "../lib/cn";
import { defaultModifierParams, modifierEnabled, modifierParams, setModifier } from "../lib/modifiers";
import type { EngineMeta, Preset, RuleField, RulesConfig, RuleValue } from "../types/api";

export function engineDefaultRules(engine: EngineMeta): RulesConfig {
  let rules: RulesConfig = {
    preset: engine.default_preset, allow_repeats: "strict", no_consecutive_actor: false,
    max_cast_order: 15, min_runtime: 0, wildcards_budget: 0,
  };
  for (const field of engine.rule_fields) rules = { ...rules, [field.key]: field.default };
  const preset = engine.presets.find((item) => item.id === engine.default_preset);
  return preset ? applyPreset(rules, engine, preset) : rules;
}

function applyPreset(value: RulesConfig, engine: EngineMeta, preset: Preset): RulesConfig {
  let next = { ...value };
  for (const field of engine.rule_fields) next = { ...next, [field.key]: field.default };
  for (const field of engine.rule_fields) {
    if (field.key in preset.values) next = { ...next, [field.key]: preset.values[field.key] };
  }
  return { ...next, preset: preset.id };
}

export default function RulesetFields({
  value, onChange, engine, castRules = true, editing = false,
}: {
  value: RulesConfig;
  onChange: (rules: RulesConfig) => void;
  engine?: EngineMeta;
  castRules?: boolean;
  editing?: boolean;
}) {
  if (!engine) return <p role="status" className="text-xs text-zinc-400">Loading mode rules…</p>;
  const fields = engine.rule_fields.filter((field) =>
    castRules || !["no_consecutive_actor", "max_cast_order"].includes(field.key),
  );
  const active = engine.presets.find((preset) => preset.id === value.preset);
  const immutable = (field: RuleField) => editing && ["track_length", "max_lives", "daily", "curses"].includes(field.key);
  function update(key: keyof RulesConfig, next: RuleValue) {
    onChange({ ...value, [key]: next, preset: "custom" });
  }
  function renderField(field: RuleField) {
    const legacyOrder = editing && field.key === "order" && value.order === undefined && "max_skip" in value;
    const legacyLength = editing && field.key === "track_length" && value.track_length === undefined;
    const current = legacyLength ? "legacy" : legacyOrder
      ? value.max_skip === null ? "free" : value.max_skip === 0 ? "strict" : value.max_skip === 2 ? "relaxed" : null
      : value[field.key] ?? field.default;
    const disabled = immutable(field);
    const id = `rule-${field.key}`;
    return (
      <div key={field.key} className="flex flex-col gap-1.5">
        <label htmlFor={id} className="flex items-center justify-between gap-3 text-xs text-zinc-300">
          {field.label}
          {field.kind === "bool" ? (
            <input id={id} type="checkbox" checked={current === true} disabled={disabled}
              onChange={(event) => update(field.key, event.target.checked)} className="accent-accent" />
          ) : field.kind === "int" ? (
            <input id={id} type="number" min={field.min ?? undefined} max={field.max ?? undefined}
              value={typeof current === "number" ? current : ""} disabled={disabled}
              onChange={(event) => update(field.key, event.target.value === "" ? null : Number(event.target.value))}
              className="w-20 rounded border border-app-border bg-app-bg px-2 py-1 text-right disabled:opacity-50" />
          ) : field.kind === "enum" ? (
            <select id={id} value={typeof current === "string" ? current : ""} disabled={disabled}
              onChange={(event) => update(field.key, event.target.value)}
              className="rounded border border-app-border bg-app-bg px-2 py-1 capitalize disabled:opacity-50">
              {legacyLength && <option value="legacy">Saved track ({value.filmography?.length ?? 0} films)</option>}
              {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          ) : null}
        </label>
        {field.kind === "segmented" && (
          <div role="radiogroup" aria-label={field.label} className="flex flex-wrap gap-2">
            {field.options.map((option) => (
              <button key={option} type="button" role="radio" aria-checked={current === option}
                disabled={disabled} onClick={() => update(field.key, option)}
                className={cn("rounded-md border px-3 py-1.5 text-xs capitalize",
                  current === option ? "border-accent text-accent" : "border-app-border text-zinc-400")}>
                {option}
              </button>
            ))}
          </div>
        )}
        {field.help && <p className="text-[11px] text-zinc-500">{field.help}</p>}
        {legacyOrder && current === null && <p className="text-[11px] text-amber-300">Legacy custom pacing: skip at most {value.max_skip} films. Choose an order to replace it.</p>}
        {disabled && <p className="text-[11px] text-zinc-500">Chosen at creation; start a new run to change this.</p>}
      </div>
    );
  }
  return (
    <section aria-label="Mode rules" className="flex flex-col gap-3 rounded-md border border-app-border p-3">
      <p className="text-xs font-semibold text-zinc-300">Mode rules · {active?.label ?? "Custom"}</p>
      <div className="flex flex-wrap gap-2">
        {engine.presets.map((preset) => (
          <button key={preset.id} type="button" aria-pressed={value.preset === preset.id}
            disabled={editing && fields.some(immutable)}
            onClick={() => onChange(applyPreset(value, engine, preset))} title={preset.blurb}
            className={cn("rounded-full border px-3 py-1.5 text-xs disabled:opacity-50",
              value.preset === preset.id ? "border-accent bg-accent/10 text-accent" : "border-app-border text-zinc-400")}>
            {preset.emoji} {preset.label}
          </button>
        ))}
      </div>
      {active && <p className="text-xs text-zinc-400">{active.blurb}</p>}
      {!editing && (
        <div className="flex flex-wrap gap-2" aria-label="Preset add-ons">
          {engine.modifiers.filter((spec) => spec.compatible && spec.scope !== "pair").map((spec) => {
            const enabled = modifierEnabled(modifierParams(spec, value));
            return (
              <button key={spec.key} type="button" aria-pressed={enabled}
                onClick={() => onChange(setModifier(value, spec, enabled ? null : defaultModifierParams(spec)))}
                className={cn("rounded-full border px-3 py-1.5 text-xs",
                  enabled ? "border-accent text-accent" : "border-app-border text-zinc-400")}>
                {enabled ? "✓" : "+"} {spec.emoji} {spec.label}
              </button>
            );
          })}
        </div>
      )}
      {fields.filter((field) => field.group === "core").map(renderField)}
      {fields.some((field) => field.group === "advanced") && (
        <details>
          <summary className="cursor-pointer text-xs text-zinc-400">Advanced rules</summary>
          <div className="mt-3 flex flex-col gap-3">{fields.filter((field) => field.group === "advanced").map(renderField)}</div>
        </details>
      )}
    </section>
  );
}
