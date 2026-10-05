import { useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Loader2 } from "lucide-react";
import RequestOptionsModal from "./RequestOptionsModal";
import Toast, { type ToastState } from "./Toast";
import Popover from "./ui/Popover";
import { ApiError, api } from "../lib/api";
import { acquisitionKey, useAcquisitionStatus, useRequestConfig } from "../lib/queries";
import type { AcquisitionStatus, RequestResult } from "../types/api";

const chipClass = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium";

/** Downloading / Requested badges, or a Request button when the movie is missing.
 * "On Server" is left to `OnServerBadge`; this renders nothing for Jellyfin-available movies. */
export default function AcquisitionControl({
  tmdbId,
  title,
  onServer,
}: {
  tmdbId: number;
  title: string;
  onServer?: boolean | null;
}) {
  const queryClient = useQueryClient();
  const { data: config } = useRequestConfig();
  const enabled = !!config?.service && onServer !== true;
  const { data: status } = useAcquisitionStatus(tmdbId, enabled);

  const [menuOpen, setMenuOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const menuId = useId();
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  function markRequested(result: RequestResult) {
    queryClient.setQueryData<AcquisitionStatus>(acquisitionKey(tmdbId), {
      state: result.state,
      source: result.service,
    });
    setToast({ type: "success", message: `Requested "${title}".` });
  }

  const quickRequest = useMutation({
    mutationFn: () =>
      config?.service === "seerr"
        ? api.post<RequestResult>("/integrations/seerr/request", { tmdb_id: tmdbId })
        : api.post<RequestResult>("/integrations/radarr/add", { tmdb_id: tmdbId, title }),
    onSuccess: markRequested,
    onError: (err) =>
      setToast({
        type: "error",
        message: err instanceof ApiError ? err.message : "Request failed.",
      }),
  });

  if (!enabled || !config?.service || !status) return null;

  const toastEl = <Toast toast={toast} onDismiss={() => setToast(null)} />;

  if (status.state === "downloading") {
    return (
      <>
        <span className={`${chipClass} bg-sky-950 text-sky-300`}>⬇ Downloading</span>
        {toastEl}
      </>
    );
  }
  if (status.state === "requested") {
    return (
      <>
        <span className={`${chipClass} bg-amber-950 text-amber-300`}>⏳ Requested</span>
        {toastEl}
      </>
    );
  }
  if (status.state === "available") {
    return status.source === "jellyfin" ? null : (
      <span className={`${chipClass} bg-emerald-950 text-emerald-400`}>✓ Available</span>
    );
  }

  const promptMode = config.request_mode === "prompt";

  return (
    <>
      <div className="inline-flex">
        <button
          type="button"
          disabled={quickRequest.isPending}
          onClick={() => (promptMode ? setModalOpen(true) : quickRequest.mutate())}
          className={`inline-flex items-center gap-1 border border-app-border px-2.5 py-1 text-[11px] font-medium text-zinc-200 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60 ${
            promptMode ? "rounded-md" : "rounded-l-md"
          }`}
        >
          {quickRequest.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : "🍿"}
          Request
        </button>
        {!promptMode && (
          <>
            <button
              ref={menuButtonRef}
              type="button"
              aria-label="Request options"
              aria-haspopup="dialog"
              aria-expanded={menuOpen}
              aria-controls={menuOpen ? menuId : undefined}
              onClick={() => setMenuOpen((v) => !v)}
              className="inline-flex items-center rounded-r-md border border-l-0 border-app-border px-1.5 text-zinc-400 transition-colors hover:bg-app-surface-hover"
            >
              <ChevronDown className="h-3 w-3" />
            </button>
            <Popover
              anchorRef={menuButtonRef}
              open={menuOpen}
              onClose={() => setMenuOpen(false)}
              label="Request options"
              panelId={menuId}
              matchAnchorWidth={false}
              className="min-w-40 p-1"
            >
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  setModalOpen(true);
                }}
                className="block w-full rounded-sm px-3 py-1.5 text-left text-xs text-zinc-200 hover:bg-app-surface-hover"
              >
                Custom Options...
              </button>
            </Popover>
          </>
        )}
      </div>
      <RequestOptionsModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        tmdbId={tmdbId}
        title={title}
        service={config.service}
        onRequested={markRequested}
      />
      {toastEl}
    </>
  );
}
