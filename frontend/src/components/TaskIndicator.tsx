import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { describeProgress, TASK_TITLES, useLiveTasks } from "../lib/tasks";
import TaskProgressBar from "./TaskProgressBar";
import Popover from "./ui/Popover";

export default function TaskIndicator() {
  const { data: tasks, live, streamError, track } = useLiveTasks();
  const active = (tasks ?? []).filter((task) => task.status === "pending" || task.status === "running");
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [cancelling, setCancelling] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const anchor = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!active.length) { setOpen(false); return; }
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active.length]);
  async function cancel(id: string) {
    setError(null);
    setCancelling((ids) => [...ids, id]);
    try { track(await api.post(`/tasks/${id}/cancel`)); }
    catch (error) { setError(error instanceof Error ? error.message : "Could not cancel task."); }
    finally { setCancelling((ids) => ids.filter((item) => item !== id)); }
  }
  if (!active.length && !streamError) return null;
  return <>
    <button ref={anchor} type="button" onClick={() => setOpen(!open)}
      aria-label={`Background tasks: ${active.length} active`} aria-expanded={open} aria-haspopup="dialog"
      className="flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs text-accent">
      <Loader2 className="h-4 w-4 animate-spin" /> {active.length}
    </button>
    <Popover anchorRef={anchor} open={open} onClose={() => setOpen(false)} label="Background tasks" matchAnchorWidth={false} className="w-80 p-3">
      <p className="mb-2 text-xs text-zinc-500">{live ? "Live progress" : "Reconnecting - refreshing every 15s"}</p>
      {(error || streamError) && <p role="alert" className="mb-2 text-xs text-red-300">{error ?? streamError}</p>}
      {active.map((task) => <section key={task.id} className="mb-3 flex flex-col gap-2 border-b border-app-border pb-3">
        <p className="break-words text-sm text-zinc-200">{task.progress_data?.label ?? TASK_TITLES[task.name] ?? task.name}</p>
        <TaskProgressBar task={task} />
        <p className="text-xs text-zinc-400">{describeProgress(task)} · {Math.max(0, Math.floor((now - Date.parse(task.created_at)) / 1000))}s</p>
        <div className="flex gap-3 text-xs">
          {task.link && <Link to={task.link} onClick={() => setOpen(false)} className="text-accent">View</Link>}
          <button type="button" disabled={task.cancel_requested || cancelling.includes(task.id)} onClick={() => void cancel(task.id)} className="text-red-300 disabled:opacity-50">
            {task.cancel_requested ? "Stopping at next checkpoint..." : "Cancel"}
          </button>
        </div>
      </section>)}
      <Link to="/settings/tasks" onClick={() => setOpen(false)} className="text-xs text-accent">Task history</Link>
    </Popover>
  </>;
}
