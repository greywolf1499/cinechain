import { useEffect, useMemo, useState } from "react";
import { confirmTableSeat, switchTableSeat, useTableSeat } from "../lib/tableMode";
import { useDeleteStep } from "../lib/queries";
import type { RunDetail, RunStep, UserSummary } from "../types/api";
import PlayerAvatar from "./PlayerAvatar";
import HandoverInterstitial from "./HandoverInterstitial";
import Toast, { type ToastState } from "./Toast";

export default function TableSeat({ run, users }: { run: RunDetail; users: UserSummary[] | undefined }) {
  const seat = useTableSeat(run.id);
  const remove = useDeleteStep(run.id);
  const [logged, setLogged] = useState<RunStep | null>(null);
  const [error, setError] = useState<ToastState | null>(null);
  useEffect(() => {
    setLogged(null);
    const receive = (event: Event) => {
      const step = (event as CustomEvent<RunStep>).detail;
      if (step.run_id === run.id) setLogged(step);
    };
    window.addEventListener("cinechain:table-log", receive);
    return () => window.removeEventListener("cinechain:table-log", receive);
  }, [run.id]);
  const nameOf = (id: string) => users?.find((u) => u.id === id)?.display_name ?? "Participant";
  const name = seat ? nameOf(seat.id) : "";
  const team = Object.entries(run.rules_config.tug_players ?? {}).find(([, id]) => id === seat?.id)?.[0];
  const pull = logged && run.rules_config.tug_momentum?.pulls.find((p) => p.step_id === logged.id);
  const actorId = String(logged?.transition_metadata?.acting_participant_id);
  const pullOwnerId = pull ? run.rules_config.tug_players?.[pull.puller] : undefined;
  const attribution = pullOwnerId && pullOwnerId !== actorId
    ? `Picked by ${nameOf(actorId)} for ${nameOf(pullOwnerId)}`
    : `Logged for ${nameOf(actorId)}`;
  const message = logged
    ? `${attribution}${pull ? ` (Team ${pull.puller === "team_a" ? "A" : "B"}) · +${pull.points} rope` : ""}`
    : "";
  const undo = remove.mutateAsync;
  const toast = useMemo<ToastState | null>(() => error ?? (logged ? {
    type: "success",
    message,
    duration: 10_000,
    action: {
      label: "Wrong player? Undo",
      onClick: async () => {
        try {
          await undo(logged.id);
          setLogged(null);
        } catch (cause) {
          setError({ type: "error", message: cause instanceof Error ? cause.message : "Undo failed." });
        }
      },
    },
  } : null), [error, logged, message, undo]);
  if (!run.rules_config.table_mode || !seat) return null;
  return <>
    <label className={`mt-2 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${team === "team_b" ? "border-blue-500/50 text-blue-200" : "border-amber-500/50 text-amber-200"}`}>
      <PlayerAvatar name={name} size="sm" />
      <span>🎮 {name}'s turn</span>
      <select aria-label="Active table participant" value={seat.id}
        onChange={(event) => switchTableSeat(run.id, event.target.value)}
        className="max-w-40 rounded bg-app-bg p-1">
        {run.participants.map((p) => <option key={p.user_id} value={p.user_id}>{nameOf(p.user_id)}</option>)}
      </select>
    </label>
    {seat.pending && <HandoverInterstitial name={name} onReady={() => confirmTableSeat(run.id)} />}
    <Toast key={logged?.id} toast={toast} onDismiss={() => { setLogged(null); setError(null); }} />
  </>;
}
