import { useState } from "react";
import { Loader2 } from "lucide-react";
import Modal from "./Modal";
import ModeOptions from "./ModeOptions";
import RulesetFields from "./RulesetFields";
import { ApiError } from "../lib/api";
import { usesCastLinks } from "../lib/gameModes";
import { useEngines, useUpdateRunRules } from "../lib/queries";
import type { RulesConfig } from "../types/api";

export default function EditRulesModal({
  open,
  onClose,
  runId,
  currentRules,
  gameType = "cinechain",
}: {
  open: boolean;
  onClose: () => void;
  runId: string;
  currentRules: RulesConfig;
  gameType?: string;
}) {
  const [rules, setRules] = useState<RulesConfig>(currentRules);
  const [error, setError] = useState<string | null>(null);
  const updateRules = useUpdateRunRules(runId);
  const engines = useEngines();

  async function handleSave() {
    setError(null);
    try {
      await updateRules.mutateAsync(rules);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update rules.");
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Edit Rules" widthClassName="max-w-md">
      <div className="flex flex-col gap-4">
        <ModeOptions gameType={gameType} value={rules} onChange={setRules}
          engine={engines.data?.find((engine) => engine.game_type === gameType)} editing />
        <RulesetFields
          value={rules}
          onChange={(next) => setRules({ ...rules, ...next })}
          castRules={usesCastLinks(gameType, rules)}
          engine={engines.data?.find((engine) => engine.game_type === gameType)}
          editing
        />
        {engines.isError && <p role="alert" className="text-xs text-red-300">Could not load mode rules. <button type="button" onClick={() => void engines.refetch()}>Retry</button></p>}

        {error && (
          <p className="rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-zinc-400 hover:bg-app-surface-hover"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={updateRules.isPending}
            onClick={handleSave}
            className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-1.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
          >
            {updateRules.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save Rules
          </button>
        </div>
      </div>
    </Modal>
  );
}
