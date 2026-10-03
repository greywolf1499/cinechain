import { useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileUp, Loader2, Rss, UserSearch, XCircle } from "lucide-react";
import TaskProgressBar from "./TaskProgressBar";
import { PASSPORT_KEY } from "../lib/queries";
import { describeProgress, type SystemTask } from "../lib/tasks";
import { useTrackedTask } from "../lib/useTrackedTask";
import type { BackfillResult, DiaryImportResult, Passport } from "../types/api";

const MAX_CSV_BYTES = 25 * 1024 * 1024;
const IMPORT_TASKS = ["diary_import_csv", "diary_import_rss"];
const REASONS: Record<string, string> = {
  no_tmdb_match: "no TMDB match",
  not_found_on_tmdb: "not found on TMDB",
  invalid_date: "no valid watched date",
  rate_limited: "TMDB rate-limited",
  tmdb_error: "TMDB error",
};

const inputClass =
  "w-full rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";
const buttonClass =
  "flex items-center justify-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50";

function TaskStatus<R>({
  task,
  error,
  unit,
  summary,
}: {
  task: SystemTask<R> | null;
  error: string | null;
  unit: string;
  summary: (result: R) => string;
}) {
  if (error) {
    return (
      <p role="alert" className="flex items-start gap-1.5 rounded-md border border-red-900/50 bg-red-950/20 px-3 py-2 text-xs text-red-300">
        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {error}
      </p>
    );
  }
  if (!task) return null;

  if (task.status === "failed") {
    const info = task.progress_data?.error;
    return (
      <p role="alert" className="flex items-start gap-1.5 rounded-md border border-red-900/50 bg-red-950/20 px-3 py-2 text-xs text-red-300">
        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          {info?.code === "import_aborted" ? "Import stopped early. " : "Failed. "}
          {task.error ?? "See Settings > Tasks & Logs."}
        </span>
      </p>
    );
  }
  if (task.status === "completed") {
    return (
      <p role="status" className="flex items-start gap-1.5 rounded-md border border-emerald-900/50 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-300">
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {task.progress_data?.result ? summary(task.progress_data.result) : "Done."}
      </p>
    );
  }

  const progress = task.progress_data?.progress;
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-app-border bg-app-bg px-3 py-2.5" role="status">
      <div className="flex items-center justify-between gap-2 text-xs text-zinc-300">
        <span className="flex min-w-0 items-center gap-1.5">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
          <span className="truncate">{task.progress_data?.label ?? "Working..."}</span>
        </span>
        <span className="shrink-0 text-zinc-500">{describeProgress(task, unit)}</span>
      </div>
      <TaskProgressBar task={task} />
      {progress?.message && <p className="text-[11px] text-zinc-500">{progress.message}</p>}
    </div>
  );
}

export default function ImportHistory({ coverage }: { coverage: Passport["directors_coverage"] }) {
  const queryClient = useQueryClient();
  const refreshPassport = () => queryClient.invalidateQueries({ queryKey: PASSPORT_KEY });

  const importer = useTrackedTask<DiaryImportResult>({ onFinished: refreshPassport, resumeNames: IMPORT_TASKS });
  const backfill = useTrackedTask<BackfillResult>({
    onFinished: refreshPassport,
    resumeNames: ["passport_backfill_directors"],
  });

  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [username, setUsername] = useState("");

  function chooseFile(next: File | undefined) {
    setFileError(null);
    if (next && next.size > MAX_CSV_BYTES) {
      setFileError("That file is over 25MB - upload diary.csv, not the whole export.");
      setFile(null);
      return;
    }
    setFile(next ?? null);
  }

  function submitCsv(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    void importer.start("/passport/import/csv", form);
    setFile(null);
    if (fileRef.current) fileRef.current.value = "";
  }

  function submitRss(event: FormEvent) {
    event.preventDefault();
    if (!username.trim()) return;
    void importer.start("/passport/import/rss", { letterboxd_username: username.trim() });
  }

  const missing = coverage.movies_total - coverage.movies_with_directors;
  const result = importer.task?.status === "completed" ? importer.task.progress_data?.result : undefined;

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-app-border bg-app-surface p-5">
      <p className="text-xs leading-relaxed text-zinc-500">
        Bring your Letterboxd history into the Passport. Imported films are saved as watched with their diary
        dates; running an import again never creates duplicates.
      </p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <form onSubmit={submitCsv} className="flex flex-col gap-2">
          <label htmlFor="diary-csv" className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
            <FileUp className="h-3.5 w-3.5" /> Diary CSV
          </label>
          <p className="text-[11px] text-zinc-600">
            Letterboxd &rarr; Settings &rarr; Import &amp; Export &rarr; Export your data, then upload{" "}
            <code>diary.csv</code>.
          </p>
          <input
            id="diary-csv"
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            disabled={importer.busy}
            onChange={(e) => chooseFile(e.target.files?.[0])}
            className="w-full text-xs text-zinc-400 file:mr-3 file:rounded-md file:border file:border-app-border file:bg-app-bg file:px-3 file:py-2 file:text-sm file:font-medium file:text-zinc-200 hover:file:bg-app-surface-hover"
          />
          {fileError && <p className="text-[11px] text-red-400">{fileError}</p>}
          <button type="submit" disabled={!file || importer.busy} className={`${buttonClass} w-fit`}>
            Import CSV
          </button>
        </form>

        <form onSubmit={submitRss} className="flex flex-col gap-2">
          <label htmlFor="diary-rss" className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
            <Rss className="h-3.5 w-3.5" /> RSS feed
          </label>
          <p className="text-[11px] text-zinc-600">
            Imports the latest diary entries from a public Letterboxd profile (about 100).
          </p>
          <div className="flex gap-2">
            <div className="relative min-w-0 flex-1">
              <UserSearch className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-600" />
              <input
                id="diary-rss"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={importer.busy}
                placeholder="Letterboxd username"
                autoComplete="off"
                className={`${inputClass} pl-8`}
              />
            </div>
            <button type="submit" disabled={!username.trim() || importer.busy} className={buttonClass}>
              Import RSS
            </button>
          </div>
        </form>
      </div>

      <TaskStatus
        task={importer.task}
        error={importer.error}
        unit="films"
        summary={(r) =>
          `Imported ${r.imported} of ${r.total_rows} entries` +
          (r.skipped_duplicates ? `, ${r.skipped_duplicates} already in your history` : "") +
          (r.unresolved_count ? `, ${r.unresolved_count} could not be matched` : "") +
          "."
        }
      />

      {result && result.unresolved_count > 0 && (
        <details className="rounded-md border border-app-border bg-app-bg px-3 py-2 text-xs text-zinc-400">
          <summary className="cursor-pointer text-zinc-300">
            {result.unresolved_count} entries could not be imported
            {result.unresolved_count > result.unresolved.length && ` (showing ${result.unresolved.length})`}
          </summary>
          <ul className="mt-2 flex flex-col gap-1">
            {result.unresolved.map((entry, index) => (
              <li key={`${entry.title}-${index}`} className="flex flex-wrap justify-between gap-2">
                <span className="break-words text-zinc-300">
                  {entry.title}
                  {entry.year ? ` (${entry.year})` : ""}
                </span>
                <span className="text-zinc-600">{REASONS[entry.reason] ?? entry.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {(missing > 0 || backfill.task || backfill.error) && (
        <div className="flex flex-col gap-2 border-t border-app-border pt-4">
          {missing > 0 && !backfill.busy && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="flex items-start gap-1.5 text-xs text-amber-300">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                Directors are known for {coverage.movies_with_directors} of {coverage.movies_total} films, so Top
                Directors is incomplete.
              </p>
              <button type="button" onClick={() => void backfill.start("/passport/backfill-directors")} className={buttonClass}>
                Look up {missing} missing {missing === 1 ? "director" : "directors"}
              </button>
            </div>
          )}
          <TaskStatus
            task={backfill.task}
            error={backfill.error}
            unit="films"
            summary={(r) => `Looked up directors for ${r.looked_up} films${r.failed ? ` (${r.failed} failed)` : ""}.`}
          />
        </div>
      )}
    </div>
  );
}
