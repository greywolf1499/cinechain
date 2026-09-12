import { useEffect } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

export interface ToastState {
  type: "success" | "error";
  message: string;
}

export default function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastState | null;
  onDismiss: () => void;
}) {
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(onDismiss, 4000);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  if (!toast) return null;

  const Icon = toast.type === "success" ? CheckCircle2 : XCircle;
  const colorClass =
    toast.type === "success"
      ? "border-emerald-800 bg-emerald-950 text-emerald-300"
      : "border-red-900 bg-red-950 text-red-300";

  return (
    <div
      className={`fixed bottom-5 right-5 z-50 flex max-w-sm items-center gap-2 rounded-md border px-4 py-3 text-sm shadow-2xl shadow-black/50 ${colorClass}`}
    >
      <Icon className="h-4 w-4 shrink-0" />
      {toast.message}
    </div>
  );
}
