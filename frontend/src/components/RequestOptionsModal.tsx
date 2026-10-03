import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import Modal from "./Modal";
import { ApiError, api } from "../lib/api";
import type { QualityProfile, RadarrOptions, RequestResult, RootFolder, SeerrOptions } from "../types/api";

const selectClass =
  "w-full rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 focus:border-accent focus:outline-none";

function folderLabel(folder: RootFolder): string {
  const name = folder.path.replace(/\/+$/, "").split("/").pop() || folder.path;
  return name === folder.path ? name : `${name} (${folder.path})`;
}

/** Explicit destination picker (server / root folder / quality profile) for a movie request. */
export default function RequestOptionsModal({
  open,
  onClose,
  tmdbId,
  title,
  service,
  onRequested,
}: {
  open: boolean;
  onClose: () => void;
  tmdbId: number;
  title: string;
  service: "seerr" | "radarr";
  onRequested: (result: RequestResult) => void;
}) {
  const seerrOptions = useQuery({
    queryKey: ["integrations", "seerr", "options"],
    queryFn: () => api.get<SeerrOptions>("/integrations/seerr/options"),
    enabled: open && service === "seerr",
  });
  const radarrOptions = useQuery({
    queryKey: ["integrations", "radarr", "profiles"],
    queryFn: () => api.get<RadarrOptions>("/integrations/radarr/profiles"),
    enabled: open && service === "radarr",
  });

  const [serverId, setServerId] = useState<number | null>(null);
  const [profileId, setProfileId] = useState<number | null>(null);
  const [rootFolder, setRootFolder] = useState("");
  const [userId, setUserId] = useState<number | null>(null);

  const servers = seerrOptions.data?.servers ?? [];
  const server = servers.find((s) => s.id === serverId) ?? null;
  const profiles: QualityProfile[] =
    service === "seerr" ? (server?.profiles ?? []) : (radarrOptions.data?.profiles ?? []);
  const folders: RootFolder[] =
    service === "seerr" ? (server?.root_folders ?? []) : (radarrOptions.data?.root_folders ?? []);

  useEffect(() => {
    if (!seerrOptions.data) return;
    const initial = seerrOptions.data.servers.find((s) => s.is_default) ?? seerrOptions.data.servers[0];
    setServerId(initial?.id ?? null);
    setUserId(seerrOptions.data.default_user_id);
  }, [seerrOptions.data]);

  useEffect(() => {
    if (service !== "seerr" || !server) return;
    setProfileId(server.active_profile_id ?? server.profiles[0]?.id ?? null);
    setRootFolder(server.active_directory ?? server.root_folders[0]?.path ?? "");
  }, [service, server]);

  useEffect(() => {
    const data = radarrOptions.data;
    if (service !== "radarr" || !data) return;
    setProfileId(data.default_quality_profile_id ?? data.profiles[0]?.id ?? null);
    setRootFolder(data.default_root_folder_path ?? data.root_folders[0]?.path ?? "");
  }, [service, radarrOptions.data]);

  const submit = useMutation({
    mutationFn: () =>
      service === "seerr"
        ? api.post<RequestResult>("/integrations/seerr/request", {
            tmdb_id: tmdbId,
            server_id: serverId,
            profile_id: profileId,
            root_folder: rootFolder || null,
            user_id: userId,
          })
        : api.post<RequestResult>("/integrations/radarr/add", {
            tmdb_id: tmdbId,
            title,
            quality_profile_id: profileId,
            root_folder_path: rootFolder,
          }),
    onSuccess: (result) => {
      onRequested(result);
      onClose();
    },
  });

  const loading = service === "seerr" ? seerrOptions.isLoading : radarrOptions.isLoading;
  const loadError = (service === "seerr" ? seerrOptions.error : radarrOptions.error) as Error | null;
  const canSubmit =
    !submit.isPending && !loading && !loadError && !!rootFolder && profileId !== null &&
    (service === "radarr" || serverId !== null);

  return (
    <Modal open={open} onClose={onClose} title={`Request "${title}"`}>
      {loading && <p className="text-sm text-zinc-500">Loading destinations...</p>}
      {loadError && <p className="text-sm text-red-400">{loadError.message}</p>}

      {!loading && !loadError && (
        <div className="flex flex-col gap-3">
          {service === "seerr" && (
            <label className="flex flex-col gap-1 text-xs text-zinc-400">
              Server
              <select
                className={selectClass}
                value={serverId ?? ""}
                onChange={(e) => setServerId(Number(e.target.value))}
              >
                {servers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.is_4k ? " (4K)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Destination folder
            <select
              className={selectClass}
              value={rootFolder}
              onChange={(e) => setRootFolder(e.target.value)}
            >
              {folders.map((f) => (
                <option key={f.path} value={f.path}>
                  {folderLabel(f)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Quality profile
            <select
              className={selectClass}
              value={profileId ?? ""}
              onChange={(e) => setProfileId(Number(e.target.value))}
            >
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>

          {service === "seerr" && (seerrOptions.data?.users.length ?? 0) > 0 && (
            <label className="flex flex-col gap-1 text-xs text-zinc-400">
              Request as
              <select
                className={selectClass}
                value={userId ?? ""}
                onChange={(e) => setUserId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">Default (API key owner)</option>
                {seerrOptions.data?.users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.display_name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {submit.error && (
            <p className="text-xs text-red-400">
              {submit.error instanceof ApiError ? submit.error.message : "Request failed."}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!canSubmit}
              onClick={() => submit.mutate()}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submit.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Request
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
