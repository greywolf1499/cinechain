import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Loader2 } from "lucide-react";
import Toast, { type ToastState } from "../Toast";
import { SettingsCard, inputClass } from "./shared";
import { ApiError, api } from "../../lib/api";
import type { SolverConfig } from "../../types/api";

export default function SolverSettingsCard() {
  const queryClient = useQueryClient();
  const { data: config } = useQuery({
    queryKey: ["settings", "solver"],
    queryFn: () => api.get<SolverConfig>("/settings/solver"),
  });
  const [value, setValue] = useState<number | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  useEffect(() => {
    if (config) setValue(config.bridge_max_duration_seconds);
  }, [config]);

  const save = useMutation({
    mutationFn: (seconds: number) =>
      api.patch<SolverConfig>("/settings/solver", { bridge_max_duration_seconds: seconds }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["settings", "solver"], updated);
      setToast({ type: "success", message: "Solver timeout saved." });
    },
    onError: (err) => {
      setToast({
        type: "error",
        message: err instanceof ApiError ? err.message : "Failed to save solver settings.",
      });
    },
  });

  return (
    <SettingsCard title="Bridge Solver">
      <div className="flex flex-col gap-2 px-5 py-4">
        <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
          <Clock className="h-3.5 w-3.5" /> Max Search Duration
        </p>
        <p className="text-xs text-zinc-600">
          How long the bridge solver searches before giving up with a clean timeout message.
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            type="number"
            min={5}
            max={600}
            value={value ?? ""}
            onChange={(e) => setValue(Number(e.target.value))}
            className={`${inputClass} w-24`}
          />
          <span className="text-xs text-zinc-500">seconds</span>
          <button
            type="button"
            disabled={save.isPending || value === null}
            onClick={() => value !== null && save.mutate(value)}
            className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save
          </button>
        </div>
        <Toast toast={toast} onDismiss={() => setToast(null)} />
      </div>
    </SettingsCard>
  );
}
