import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { SettingsCard } from "./shared";
import { useEngines } from "../../lib/queries";

/** Read-only JSON view of the built-in rule presets (rules themselves are edited per run). */
export default function RulesetsCard() {
  const [copied, setCopied] = useState<string | null>(null);
  const engines = useEngines();

  async function copy(id: string, json: string) {
    await navigator.clipboard?.writeText(json);
    setCopied(id);
    setTimeout(() => setCopied(null), 1500);
  }

  return (
    <SettingsCard title="JSON Rulesets">
      <div className="flex flex-col gap-4 px-5 py-4">
        <p className="text-xs text-zinc-500">
          The presets a new run can start from. A run stores its own copy as <code>rules_config</code>, so
          changing a run&apos;s rules (Edit Rules on the run page) never alters these.
        </p>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
          {engines.isError && <p role="alert">Could not load presets. <button type="button" onClick={() => void engines.refetch()}>Retry</button></p>}
          {engines.isPending && <p role="status">Loading presets…</p>}
          {(engines.data?.find((engine) => engine.game_type === "cinechain")?.presets ?? []).map((preset) => {
            const id = preset.id;
            const json = JSON.stringify({ ...preset.values, preset: id }, null, 2);
            return (
              <div key={id} className="min-w-0 rounded-md border border-app-border bg-app-bg">
                <div className="flex items-center justify-between border-b border-app-border px-3 py-1.5">
                  <span className="text-xs font-medium capitalize text-zinc-300">{id}</span>
                  <button
                    type="button"
                    onClick={() => copy(id, json)}
                    className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-200"
                  >
                    {copied === id ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                    {copied === id ? "Copied" : "Copy"}
                  </button>
                </div>
                <pre className="overflow-x-auto px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-400">
                  {json}
                </pre>
              </div>
            );
          })}
        </div>
      </div>
    </SettingsCard>
  );
}
