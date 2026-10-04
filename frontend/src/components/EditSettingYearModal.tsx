import { useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import Modal from "./Modal";
import { ApiError } from "../lib/api";
import { formatNarrativeYear, parseNarrativeYear } from "../lib/historicalEra";
import { useUpdateNarrativeEra } from "../lib/queries";

/** Corrects when a film is *set* (Historical Time-Travel), for when TMDB's keywords were approximate. */
export default function EditSettingYearModal({
  open,
  onClose,
  runId,
  movieId,
  movieTitle,
  year,
  label,
}: {
  open: boolean;
  onClose: () => void;
  runId: string;
  movieId: number;
  movieTitle: string;
  year: number | null | undefined;
  label: string | null | undefined;
}) {
  const [yearText, setYearText] = useState(year != null ? String(year) : "");
  const [labelText, setLabelText] = useState(label ?? "");
  const [error, setError] = useState<string | null>(null);
  const update = useUpdateNarrativeEra(runId);

  const parsed = parseNarrativeYear(yearText);
  const yearInvalid = yearText.trim() !== "" && parsed === null;
  const canSave = !update.isPending && !yearInvalid && (parsed !== null || labelText.trim() !== "");

  async function save() {
    setError(null);
    try {
      await update.mutateAsync({
        movieId,
        ...(parsed !== null ? { narrative_year: parsed } : {}),
        ...(labelText.trim() ? { narrative_era_label: labelText.trim() } : {}),
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the setting year.");
    }
  }

  async function redetect() {
    setError(null);
    try {
      const result = await update.mutateAsync({ movieId });
      setYearText(String(result.narrative_year));
      setLabelText(result.narrative_era_label);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't re-detect the setting year.");
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={`Edit Setting Year - ${movieTitle}`} widthClassName="max-w-sm">
      <div className="flex flex-col gap-4">
        <p className="text-xs text-zinc-500">
          The year the story takes place in, not the release year. Negative years (or "BC") are before the common era.
        </p>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
          Setting year
          <input
            value={yearText}
            onChange={(e) => setYearText(e.target.value)}
            placeholder="e.g. 1943, 180, -400 or 400 BC"
            aria-invalid={yearInvalid}
            className="rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 focus:border-accent focus:outline-none aria-[invalid=true]:border-red-500"
          />
          {yearInvalid && <span className="text-[11px] text-red-400">Enter a year between -10,000 and 10,000.</span>}
          {parsed !== null && (
            <span className="text-[11px] text-zinc-500">Reads as {formatNarrativeYear(parsed)}</span>
          )}
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
          Era label
          <input
            value={labelText}
            onChange={(e) => setLabelText(e.target.value)}
            maxLength={60}
            placeholder="e.g. Ancient Rome, World War II"
            className="rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 focus:border-accent focus:outline-none"
          />
        </label>
        {error && (
          <p role="alert" className="text-xs text-red-400">
            {error}
          </p>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={redetect}
            disabled={update.isPending}
            title="Work the year out again from TMDB keywords and the plot"
            className="flex items-center gap-1.5 rounded-md border border-app-border px-3 py-2 text-xs text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:opacity-60"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Re-detect
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!canSave}
            className="ml-auto flex items-center gap-1.5 rounded-md bg-accent px-4 py-2 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-50"
          >
            {update.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save
          </button>
        </div>
      </div>
    </Modal>
  );
}
