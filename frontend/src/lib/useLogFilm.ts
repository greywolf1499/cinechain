import { useMutation } from "@tanstack/react-query";
import { api } from "./api";
import { useCreateStep, useDeleteStep, useMarkStepWatched } from "./queries";
import type { RunStep, ValidationResult } from "../types/api";
import type { ToastState } from "../components/Toast";

export const FILM_TOAST_EVENT = "cinechain:film-toast";

export function filmToast(toast: ToastState) {
  window.dispatchEvent(new CustomEvent<ToastState>(FILM_TOAST_EVENT, { detail: toast }));
}

type LogPayload = Parameters<ReturnType<typeof useCreateStep>["mutateAsync"]>[0];
export type WatchExtras = Omit<Parameters<ReturnType<typeof useMarkStepWatched>["mutateAsync"]>[0], "stepId">;
type StepRef = Pick<RunStep, "id">;
type Action =
  | { kind: "create"; payload: LogPayload }
  | { kind: "watch"; step: StepRef; extras: WatchExtras }
  | { kind: "unqueue"; step: StepRef };

export function useLogFilm(runId: string) {
  // These mutations own actingFields, Table Mode handover and query invalidation.
  const create = useCreateStep(runId);
  const watch = useMarkStepWatched(runId);
  const remove = useDeleteStep(runId);
  const action = useMutation({
    mutationFn: async (action: Action) => {
      if (action.kind === "unqueue") {
        await remove.mutateAsync(action.step.id);
        return null;
      }
      if (action.kind === "watch") {
        return watch.mutateAsync({ stepId: action.step.id, watched_at: new Date().toISOString(), ...action.extras });
      }
      const payload = { ...action.payload };
      const result = await api.post<ValidationResult>(`/runs/${runId}/validate`, {
        movie_id: payload.movie_id, tunnel_side: payload.tunnel_side,
      });
      const skips = result.overlay_skippable ?? [];
      if (!result.valid && !payload.no_contest) {
        if (skips.length && !result.blocked && payload.status !== "planned") {
          if (!window.confirm(`Spend ${skips.length} wildcard${skips.length === 1 ? "" : "s"} to skip this unreachable requirement and log watched?`)) {
            throw new Error("Logging cancelled; no wildcard was spent.");
          }
          payload.force = true;
          payload.skip_overlays = skips;
        } else if (result.blocked || !payload.force) {
          throw new Error(result.reason ?? "This film does not meet the run's rules.");
        }
      }
      return create.mutateAsync(payload);
    },
    onSuccess: (step, action) => filmToast({
      type: "success",
      message: action.kind === "unqueue" ? "Film removed from Up next."
        : step?.status === "planned" ? `${step.movie_title} · Up next`
        : `${step?.movie_title ?? "Film"} · Watched`,
    }),
    onError: (error) => filmToast({ type: "error", message: error.message }),
  });
  async function log(payload: LogPayload) {
    const step = await action.mutateAsync({ kind: "create", payload });
    if (!step) throw new Error("The film was not logged.");
    return step;
  }
  async function markWatched(step: StepRef, extras: WatchExtras = {}) {
    const watched = await action.mutateAsync({ kind: "watch", step, extras });
    if (!watched) throw new Error("The film was not marked watched.");
    return watched;
  }
  return {
    ...action,
    pendingMovieId: action.isPending && action.variables?.kind === "create" ? action.variables.payload.movie_id : null,
    // Payload-based callers retain their mode-specific connection evidence.
    mutateAsync: log,
    queue: (movieId: number, extras: Omit<LogPayload, "movie_id" | "status"> = {}) =>
      log({ ...extras, movie_id: movieId, status: "planned", watched_at: null }),
    logWatched: (movieId: number, extras: Omit<LogPayload, "movie_id" | "status"> = {}) =>
      log({ watched_at: new Date().toISOString(), ...extras, movie_id: movieId, status: "watched" }),
    markWatched,
    unqueue: (step: StepRef) => action.mutateAsync({ kind: "unqueue", step }),
  };
}
