import { useEffect, useRef } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

export interface ToastState {
  type: "success" | "error";
  message: string;
  duration?: number;
  action?: { label: string; onClick: () => void };
}

export default function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastState | null;
  onDismiss: () => void;
}) {
  const dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => dismiss.current(), toast.duration ?? 4000);
    return () => clearTimeout(timer);
  }, [toast]);

  if (!toast) return null;

  const Icon = toast.type === "success" ? CheckCircle2 : XCircle;
  const colorClass =
    toast.type === "success"
      ? "border-emerald-800 bg-emerald-950 text-emerald-300"
      : "border-red-900 bg-red-950 text-red-300";

  return (
    <div
      role={toast.type === "error" ? "alert" : "status"}
      className={`fixed bottom-5 right-5 z-50 flex max-w-[calc(100vw-2.5rem)] items-center gap-2 rounded-md border px-4 py-3 text-sm shadow-2xl shadow-black/50 sm:max-w-sm ${colorClass}`}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="min-w-0 break-words">{toast.message}</span>
      {toast.action && <button type="button" className="shrink-0 underline" onClick={toast.action.onClick}>{toast.action.label}</button>}
    </div>
  );
}
