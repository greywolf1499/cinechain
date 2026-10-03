import { AlertTriangle, Check } from "lucide-react";
import { cn } from "../lib/cn";
import type { RawRulesConfig } from "../types/api";

export type RawRulesParse = { value: RawRulesConfig; error: null } | { value: null; error: string };

/** Syntax + shape check only; the backend still validates the condition blocks. */
export function parseRawRules(text: string): RawRulesParse {
  if (!text.trim()) return { value: null, error: "Paste a JSON object." };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    return { value: null, error: err instanceof Error ? err.message : "Invalid JSON." };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { value: null, error: "The ruleset must be a JSON object ({ ... })." };
  }
  return { value: parsed as RawRulesConfig, error: null };
}

export const RAW_RULES_EXAMPLE: RawRulesConfig = {
  preset: "custom",
  allow_repeats: "strict",
  no_consecutive_actor: true,
  max_cast_order: 15,
  min_runtime: 40,
  wildcards_budget: 2,
  win_condition: { type: "decades_spanned", count: 3 },
  fail_condition: { type: "max_wildcards_used", count: 3 },
};

export default function RawRulesEditor({
  text,
  onChange,
}: {
  text: string;
  onChange: (text: string) => void;
}) {
  const { error } = parseRawRules(text);

  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
        Raw ruleset (JSON)
        <textarea
          value={text}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          rows={12}
          aria-invalid={error !== null}
          className={cn(
            "resize-y rounded-md border bg-app-bg px-3 py-2 font-mono text-xs leading-relaxed text-zinc-100 focus:outline-none focus:ring-1",
            error
              ? "border-red-900 focus:border-red-700 focus:ring-red-700"
              : "border-app-border focus:border-accent focus:ring-accent",
          )}
        />
      </label>
      {error ? (
        <p className="flex items-start gap-1.5 text-xs text-red-400">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      ) : (
        <p className="flex items-center gap-1.5 text-xs text-emerald-400">
          <Check className="h-3.5 w-3.5" />
          Valid JSON
        </p>
      )}
      <p className="text-[11px] leading-snug text-zinc-500">
        Sent to the engine as-is, bypassing the form. Supported opt-in conditions:{" "}
        <code className="text-zinc-400">win_condition</code> (decades_spanned, countries_visited,
        movies_watched) and <code className="text-zinc-400">fail_condition</code> (max_wildcards_used,
        max_repeats_used, max_same_actor_links). Each takes <code className="text-zinc-400">{"{ type, count }"}</code>{" "}
        or a list of them.
      </p>
    </div>
  );
}
