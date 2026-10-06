import { useSyncExternalStore } from "react";
import { useAuthStore } from "../store/authStore";
import type { RunDetail, RunStep } from "../types/api";

const listeners = new Set<() => void>();
const key = (runId: string) => `cinechain.table.${useAuthStore.getState().user?.id ?? "guest"}.${runId}`;
type Seat = { id: string; pending: boolean; signature: string; offerer?: string };
const read = (runId: string): Seat | null => {
  const value = sessionStorage.getItem(key(runId));
  return value ? JSON.parse(value) : null;
};
function write(runId: string, seat: Seat) {
  sessionStorage.setItem(key(runId), JSON.stringify(seat));
  listeners.forEach((listener) => listener());
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function syncTableRun(run: RunDetail) {
  if (!run.rules_config.table_mode) {
    sessionStorage.removeItem(key(run.id));
    listeners.forEach((listener) => listener());
    return;
  }
  const previous = read(run.id);
  const members = run.participants.map((p) => p.user_id);
  const owner = run.participants.find((p) => p.role === "owner")?.user_id ?? members[0];
  if (!owner) return;
  const fork = run.rules_config.pending_fork;
  const momentum = run.rules_config.tug_momentum;
  const signature = fork ? `fork:${fork.offered_at}` : run.game_type === "tug_of_war"
    ? `tug:${momentum?.pulls.at(-1)?.step_id ?? ""}:${momentum?.next_team ?? "team_a"}`
    : "free";
  if (previous?.signature === signature && members.includes(previous.id)) return;
  const target = fork
    ? members.find((id) => id !== fork.offered_by_id)
    : run.game_type === "tug_of_war"
      ? run.rules_config.tug_players?.[momentum?.next_team ?? "team_a"]
      : previous?.offerer ?? previous?.id ?? owner;
  const id = target && members.includes(target) ? target : owner;
  write(run.id, { id, signature, pending: !!fork || (!!previous && previous.id !== id),
    ...(fork ? { offerer: fork.offered_by_id } : {}) });
}

export function actingFields(runId: string): { acting_participant_id?: string } {
  const seat = read(runId);
  if (seat?.pending) throw new Error("Pass the device and confirm the active participant first.");
  return seat ? { acting_participant_id: seat.id } : {};
}
export function useTableSeat(runId: string) {
  const value = useSyncExternalStore(subscribe, () => sessionStorage.getItem(key(runId)), () => null);
  return value ? JSON.parse(value) as Seat : null;
}
export function switchTableSeat(runId: string, id: string) {
  const seat = read(runId);
  if (seat && seat.id !== id) write(runId, { ...seat, id, pending: true });
}
export function confirmTableSeat(runId: string) {
  const seat = read(runId);
  if (seat) write(runId, { ...seat, pending: false });
}
export function notifyTableLog(step: RunStep) {
  if (step.transition_metadata?.acting_participant_id) {
    window.dispatchEvent(new CustomEvent<RunStep>("cinechain:table-log", { detail: step }));
  }
}
