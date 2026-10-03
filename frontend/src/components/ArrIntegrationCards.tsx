import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import Toast, { type ToastState } from "./Toast";
import { ApiError, api } from "../lib/api";
import type {
  ConnectivityTestResult,
  IntegrationConfig,
  RadarrOptions,
  RequestMode,
  SeerrOptions,
} from "../types/api";

const inputClass =
  "rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";
const secondaryButton =
  "flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60";
const primaryButton =
  "flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60";

const CONFIG_KEY = ["settings", "integrations"] as const;

function useIntegrationConfig() {
  return useQuery({
    queryKey: CONFIG_KEY,
    queryFn: () => api.get<IntegrationConfig>("/settings/integrations"),
  });
}

function folderName(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() || path;
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-app-border bg-app-surface">
      <div className="border-b border-app-border px-5 py-3 text-sm font-medium text-zinc-300">
        {title}
      </div>
      <div className="flex flex-col gap-4 px-5 py-4">{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
      {label}
      {children}
    </label>
  );
}

function TestResult({ result }: { result: ConnectivityTestResult | null }) {
  if (!result) return null;
  return (
    <span
      className={`flex items-center gap-1.5 text-xs ${result.reachable ? "text-emerald-400" : "text-red-400"}`}
    >
      {result.reachable ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
      {result.reachable
        ? `Connected${result.version ? ` (v${result.version})` : ""}`
        : (result.detail ?? "Failed")}
    </span>
  );
}

type ConfigPatch = Partial<
  Record<
    | "radarr_url"
    | "radarr_api_key"
    | "radarr_default_root_folder_path"
    | "seerr_url"
    | "seerr_api_key"
    | "seerr_request_mode",
    string | null
  > &
    Record<"radarr_default_quality_profile_id" | "seerr_user_id", number | null>
>;

function useSaveConfig(onToast: (toast: ToastState) => void, successMessage: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ConfigPatch) => api.patch<IntegrationConfig>("/settings/integrations", payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(CONFIG_KEY, updated);
      queryClient.invalidateQueries({ queryKey: ["integrations"] });
      onToast({ type: "success", message: successMessage });
    },
    onError: (err) => onToast({ type: "error", message: errorText(err, "Failed to save settings.") }),
  });
}

export function RadarrSettingsCard() {
  const { data: config } = useIntegrationConfig();
  const [toast, setToast] = useState<ToastState | null>(null);
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [profileId, setProfileId] = useState<number | null>(null);
  const [rootFolder, setRootFolder] = useState("");
  const [result, setResult] = useState<ConnectivityTestResult | null>(null);

  useEffect(() => {
    if (!config) return;
    setUrl(config.radarr_url);
    setProfileId(config.radarr_default_quality_profile_id);
    setRootFolder(config.radarr_default_root_folder_path ?? "");
  }, [config]);

  const options = useQuery({
    queryKey: ["integrations", "radarr", "profiles"],
    queryFn: () => api.get<RadarrOptions>("/integrations/radarr/profiles"),
    enabled: !!config?.radarr_configured,
    retry: false,
  });

  const test = useMutation({
    mutationFn: () =>
      api.post<ConnectivityTestResult>("/settings/integrations/test-radarr", {
        url: url.trim(),
        api_key: apiKey.trim() || undefined,
      }),
    onSuccess: setResult,
    onError: (err) => setToast({ type: "error", message: errorText(err, "Radarr test failed.") }),
  });
  const save = useSaveConfig(setToast, "Radarr settings saved.");

  function handleSave() {
    save.mutate({
      radarr_url: url.trim(),
      ...(apiKey.trim() ? { radarr_api_key: apiKey.trim() } : {}),
      radarr_default_quality_profile_id: profileId,
      radarr_default_root_folder_path: rootFolder || null,
    });
    setApiKey("");
  }

  return (
    <Card title="Radarr">
      <p className="text-xs text-zinc-600">
        Used for one-click requests when Seerr is not configured. Set a default profile and folder
        to skip the destination prompt.
      </p>
      <div className="flex flex-col gap-3 sm:flex-row">
        <Field label="URL">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://radarr:7878"
            className={`${inputClass} normal-case`}
          />
        </Field>
        <Field label="API Key">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={config?.radarr_configured ? `Currently set: ${config.radarr_api_key_masked}` : "API key"}
            className={`${inputClass} normal-case`}
          />
        </Field>
      </div>

      {options.data?.enabled && (
        <div className="flex flex-col gap-3 sm:flex-row">
          <Field label="Default quality profile">
            <select
              value={profileId ?? ""}
              onChange={(e) => setProfileId(e.target.value ? Number(e.target.value) : null)}
              className={`${inputClass} normal-case`}
            >
              <option value="">None (ask each time)</option>
              {options.data.profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Default root folder">
            <select
              value={rootFolder}
              onChange={(e) => setRootFolder(e.target.value)}
              className={`${inputClass} normal-case`}
            >
              <option value="">None (ask each time)</option>
              {options.data.root_folders.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.path}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
      {options.error && (
        <p className="text-xs text-amber-400">Could not load Radarr options: {(options.error as Error).message}</p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => test.mutate()} disabled={test.isPending || !url.trim()} className={secondaryButton}>
          {test.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Test Connection
        </button>
        <button type="button" onClick={handleSave} disabled={save.isPending} className={primaryButton}>
          Save
        </button>
        <TestResult result={result} />
      </div>
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </Card>
  );
}

export function SeerrSettingsCard() {
  const { data: config } = useIntegrationConfig();
  const [toast, setToast] = useState<ToastState | null>(null);
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [mode, setMode] = useState<RequestMode>("auto");
  const [userId, setUserId] = useState<number | null>(null);
  const [result, setResult] = useState<ConnectivityTestResult | null>(null);

  useEffect(() => {
    if (!config) return;
    setUrl(config.seerr_url);
    setMode(config.seerr_request_mode);
    setUserId(config.seerr_user_id);
  }, [config]);

  const options = useQuery({
    queryKey: ["integrations", "seerr", "options"],
    queryFn: () => api.get<SeerrOptions>("/integrations/seerr/options"),
    enabled: !!config?.seerr_configured,
    retry: false,
  });

  const test = useMutation({
    mutationFn: () =>
      api.post<ConnectivityTestResult>("/settings/integrations/test-seerr", {
        url: url.trim(),
        api_key: apiKey.trim() || undefined,
      }),
    onSuccess: setResult,
    onError: (err) => setToast({ type: "error", message: errorText(err, "Seerr test failed.") }),
  });
  const save = useSaveConfig(setToast, "Seerr settings saved.");

  function handleSave() {
    save.mutate({
      seerr_url: url.trim(),
      ...(apiKey.trim() ? { seerr_api_key: apiKey.trim() } : {}),
      seerr_request_mode: mode,
      seerr_user_id: userId,
    });
    setApiKey("");
  }

  const folders = [
    ...new Set((options.data?.servers ?? []).flatMap((s) => s.root_folders.map((f) => folderName(f.path)))),
  ];

  return (
    <Card title="Seerr / Jellyseerr">
      <div className="flex flex-col gap-3 sm:flex-row">
        <Field label="URL">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://seerr:5055"
            className={`${inputClass} normal-case`}
          />
        </Field>
        <Field label="API Key">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={config?.seerr_configured ? `Currently set: ${config.seerr_api_key_masked}` : "API key"}
            className={`${inputClass} normal-case`}
          />
        </Field>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row">
        <Field label="Request mode">
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as RequestMode)}
            className={`${inputClass} normal-case`}
          >
            <option value="auto">Auto-Route (Use Seerr Language Rules)</option>
            <option value="prompt">Always Prompt (Select Folder/Server)</option>
          </select>
        </Field>
        <Field label="Request on behalf of">
          <select
            value={userId ?? ""}
            onChange={(e) => setUserId(e.target.value ? Number(e.target.value) : null)}
            disabled={!options.data?.users.length}
            className={`${inputClass} normal-case`}
          >
            <option value="">Default (API key owner)</option>
            {options.data?.users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name}
                {u.email ? ` (${u.email})` : ""}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {options.data?.enabled && (
        <div className="text-xs text-zinc-500">
          <p>
            Detected servers:{" "}
            <span className="text-zinc-300">
              {options.data.servers.map((s) => s.name).join(", ") || "none"}
            </span>
          </p>
          <p className="mt-0.5">
            Detected folders: <span className="text-zinc-300">{folders.join(", ") || "none"}</span>
          </p>
        </div>
      )}
      {options.error && (
        <p className="text-xs text-amber-400">Could not load Seerr options: {(options.error as Error).message}</p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => test.mutate()} disabled={test.isPending || !url.trim()} className={secondaryButton}>
          {test.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Test Connection
        </button>
        <button type="button" onClick={handleSave} disabled={save.isPending} className={primaryButton}>
          Save
        </button>
        <TestResult result={result} />
      </div>
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </Card>
  );
}
