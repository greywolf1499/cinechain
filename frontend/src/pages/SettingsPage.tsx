import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, HelpCircle, Loader2, XCircle } from "lucide-react";
import PageHeading from "../components/PageHeading";
import Toast, { type ToastState } from "../components/Toast";
import { ApiError, api } from "../lib/api";
import { queryKeys, useCacheStats, useUsers } from "../lib/queries";
import { useAuthStore } from "../store/authStore";
import type {
  ConnectivityTestResult,
  IntegrationConfig,
  IntegrationStatus,
  User,
} from "../types/api";

const inputClass =
  "rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";

export default function SettingsPage() {
  const currentUser = useAuthStore((s) => s.user);
  const { data, isLoading } = useQuery({
    queryKey: ["integrations", "status"],
    queryFn: () => api.get<IntegrationStatus>("/integrations/status"),
  });
  const { data: cacheStats, isLoading: cacheLoading } = useCacheStats();

  return (
    <div>
      <PageHeading title="Settings" subtitle="Homelab integrations and system status" />

      <div className="flex flex-col gap-5">
        <SettingsCard title="Integrations">
          {isLoading && <div className="px-5 py-4 text-sm text-zinc-500">Loading...</div>}
          {data && (
            <div className="divide-y divide-app-border">
              <IntegrationRow
                name="Jellyfin"
                enabled={data.jellyfin.enabled}
                detail={
                  data.jellyfin.enabled
                    ? data.jellyfin.reachable
                      ? `Reachable${data.jellyfin.version ? ` (v${data.jellyfin.version})` : ""}`
                      : "Configured but unreachable"
                    : "Not configured"
                }
                ok={data.jellyfin.enabled && data.jellyfin.reachable}
              />
              <IntegrationRow
                name="Radarr"
                enabled={data.radarr.enabled}
                detail={data.radarr.implemented ? "Available" : "Not implemented yet (v1.1)"}
                ok={null}
              />
              <IntegrationRow
                name="Seerr"
                enabled={data.seerr.enabled}
                detail={data.seerr.implemented ? "Available" : "Not implemented yet (v1.1)"}
                ok={null}
              />
            </div>
          )}
          {currentUser?.is_admin && <IntegrationSettingsEditor />}
        </SettingsCard>

        <SettingsCard title="Cache Stats">
          {cacheLoading && <div className="px-5 py-4 text-sm text-zinc-500">Loading...</div>}
          {cacheStats && (
            <div className="grid grid-cols-2 gap-4 px-5 py-4 sm:grid-cols-4">
              <MiniStat label="Cached Movies" value={cacheStats.cached_movies} />
              <MiniStat label="Cached Actors" value={cacheStats.cached_actors} />
              <MiniStat label="Cast Edges" value={cacheStats.cached_cast_edges} />
              <MiniStat
                label="DB Size on Disk"
                value={cacheStats.db_size_bytes != null ? formatBytes(cacheStats.db_size_bytes) : "-"}
              />
            </div>
          )}
        </SettingsCard>

        <UsersCard />
      </div>
    </div>
  );
}

function SettingsCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-app-border bg-app-surface">
      <div className="border-b border-app-border px-5 py-3 text-sm font-medium text-zinc-300">
        {title}
      </div>
      {children}
    </div>
  );
}

function IntegrationSettingsEditor() {
  const queryClient = useQueryClient();
  const { data: config } = useQuery({
    queryKey: ["settings", "integrations"],
    queryFn: () => api.get<IntegrationConfig>("/settings/integrations"),
  });

  const [toast, setToast] = useState<ToastState | null>(null);
  const [tmdbToken, setTmdbToken] = useState("");
  const [tmdbResult, setTmdbResult] = useState<ConnectivityTestResult | null>(null);
  const [jellyfinUrl, setJellyfinUrl] = useState("");
  const [jellyfinKey, setJellyfinKey] = useState("");
  const [jellyfinResult, setJellyfinResult] = useState<ConnectivityTestResult | null>(null);

  useEffect(() => {
    if (config) setJellyfinUrl(config.jellyfin_url);
  }, [config?.jellyfin_url]);

  const testTmdb = useMutation({
    mutationFn: (tmdb_api_key: string) =>
      api.post<ConnectivityTestResult>("/settings/integrations/test-tmdb", { tmdb_api_key }),
  });
  const testJellyfin = useMutation({
    mutationFn: (payload: { jellyfin_url: string; jellyfin_api_key?: string }) =>
      api.post<ConnectivityTestResult>("/settings/integrations/test-jellyfin", payload),
  });
  const saveIntegrations = useMutation({
    mutationFn: (payload: Partial<Record<"tmdb_api_key" | "jellyfin_url" | "jellyfin_api_key", string>>) =>
      api.patch<IntegrationConfig>("/settings/integrations", payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(["settings", "integrations"], updated);
    },
  });

  async function handleTmdbTestAndSave() {
    const token = tmdbToken.trim();
    if (!token) {
      setToast({ type: "error", message: "Enter a TMDB token first." });
      return;
    }
    try {
      const result = await testTmdb.mutateAsync(token);
      setTmdbResult(result);
      if (!result.reachable) {
        setToast({ type: "error", message: result.detail ?? "TMDB connection failed." });
        return;
      }
      await saveIntegrations.mutateAsync({ tmdb_api_key: token });
      setTmdbToken("");
      setToast({ type: "success", message: "TMDB connected and saved." });
    } catch (err) {
      setToast({
        type: "error",
        message: err instanceof ApiError ? err.message : "TMDB test failed.",
      });
    }
  }

  async function handleJellyfinTest() {
    const url = jellyfinUrl.trim();
    if (!url) {
      setToast({ type: "error", message: "Enter a Jellyfin URL first." });
      return;
    }
    try {
      const result = await testJellyfin.mutateAsync({
        jellyfin_url: url,
        jellyfin_api_key: jellyfinKey.trim() || undefined,
      });
      setJellyfinResult(result);
      setToast(
        result.reachable
          ? { type: "success", message: `Connected${result.version ? ` (v${result.version})` : ""}.` }
          : { type: "error", message: result.detail ?? "Jellyfin connection failed." },
      );
    } catch (err) {
      setToast({
        type: "error",
        message: err instanceof ApiError ? err.message : "Jellyfin test failed.",
      });
    }
  }

  async function handleJellyfinSave() {
    try {
      await saveIntegrations.mutateAsync({
        jellyfin_url: jellyfinUrl.trim(),
        ...(jellyfinKey.trim() ? { jellyfin_api_key: jellyfinKey.trim() } : {}),
      });
      setJellyfinKey("");
      setToast({ type: "success", message: "Jellyfin settings saved." });
    } catch (err) {
      setToast({
        type: "error",
        message: err instanceof ApiError ? err.message : "Failed to save Jellyfin settings.",
      });
    }
  }

  return (
    <div className="flex flex-col gap-5 border-t border-app-border px-5 py-4">
      <div>
        <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
          TMDB API Token
        </p>
        <p className="mb-2 text-xs text-zinc-500">
          {config?.tmdb_configured
            ? `Currently set: ${config.tmdb_api_key_masked}`
            : "Not configured"}
        </p>
        <div className="flex flex-wrap gap-2">
          <input
            type="password"
            value={tmdbToken}
            onChange={(e) => setTmdbToken(e.target.value)}
            placeholder="Enter a new v4 read access token..."
            className={`${inputClass} min-w-[240px] flex-1`}
          />
          <button
            type="button"
            onClick={handleTmdbTestAndSave}
            disabled={testTmdb.isPending || saveIntegrations.isPending}
            className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            {(testTmdb.isPending || saveIntegrations.isPending) && (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            )}
            Test &amp; Save
          </button>
        </div>
        {tmdbResult && (
          <p
            className={`mt-1.5 flex items-center gap-1.5 text-xs ${tmdbResult.reachable ? "text-emerald-400" : "text-red-400"}`}
          >
            {tmdbResult.reachable ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <XCircle className="h-3.5 w-3.5" />
            )}
            {tmdbResult.reachable ? "Connected" : tmdbResult.detail ?? "Failed"}
          </p>
        )}
      </div>

      <div>
        <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">Jellyfin</p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={jellyfinUrl}
            onChange={(e) => setJellyfinUrl(e.target.value)}
            placeholder="http://jellyfin:8096"
            className={`${inputClass} flex-1`}
          />
          <input
            type="password"
            value={jellyfinKey}
            onChange={(e) => setJellyfinKey(e.target.value)}
            placeholder={
              config?.jellyfin_configured
                ? `Currently set: ${config.jellyfin_api_key_masked}`
                : "API key"
            }
            className={`${inputClass} flex-1`}
          />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={handleJellyfinTest}
            disabled={testJellyfin.isPending}
            className="flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
          >
            {testJellyfin.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Test Connection
          </button>
          <button
            type="button"
            onClick={handleJellyfinSave}
            disabled={saveIntegrations.isPending}
            className="rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            Save
          </button>
          {jellyfinResult && (
            <span
              className={`flex items-center gap-1.5 text-xs ${jellyfinResult.reachable ? "text-emerald-400" : "text-red-400"}`}
            >
              {jellyfinResult.reachable ? (
                <CheckCircle2 className="h-3.5 w-3.5" />
              ) : (
                <XCircle className="h-3.5 w-3.5" />
              )}
              {jellyfinResult.reachable
                ? `Connected${jellyfinResult.version ? ` (v${jellyfinResult.version})` : ""}`
                : jellyfinResult.detail ?? "Failed"}
            </span>
          )}
        </div>
      </div>

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <p className="text-lg font-semibold text-zinc-100">{value}</p>
      <p className="mt-0.5 text-xs text-zinc-500">{label}</p>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function UsersCard() {
  const currentUser = useAuthStore((s) => s.user);
  const { data: users, isLoading } = useUsers();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const registerUser = useMutation({
    mutationFn: () =>
      api.post<User>("/auth/register", {
        username,
        password,
        display_name: displayName,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users });
      setUsername("");
      setDisplayName("");
      setPassword("");
      setShowForm(false);
      setError(null);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Failed to register user.");
    },
  });

  return (
    <SettingsCard title="Users">
      {isLoading && <div className="px-5 py-4 text-sm text-zinc-500">Loading...</div>}
      {users && (
        <div className="divide-y divide-app-border">
          {users.map((user) => (
            <div key={user.id} className="px-5 py-3">
              <p className="text-sm text-zinc-200">{user.display_name}</p>
              <p className="text-xs text-zinc-500">@{user.username}</p>
            </div>
          ))}
        </div>
      )}

      {currentUser?.is_admin && (
        <div className="border-t border-app-border px-5 py-4">
          {!showForm ? (
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="text-sm font-medium text-accent hover:underline"
            >
              + Register a new participant
            </button>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                registerUser.mutate();
              }}
              className="flex flex-col gap-2.5"
            >
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Display name"
                required
                className={inputClass}
              />
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Username"
                required
                minLength={3}
                className={inputClass}
              />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password (min 8 characters)"
                required
                minLength={8}
                className={inputClass}
              />
              {error && <p className="text-xs text-red-400">{error}</p>}
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={registerUser.isPending}
                  className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
                >
                  {registerUser.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  Create account
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowForm(false);
                    setError(null);
                  }}
                  className="rounded-md px-3 py-1.5 text-sm text-zinc-400 hover:bg-app-surface-hover"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </SettingsCard>
  );
}

function IntegrationRow({
  name,
  enabled,
  detail,
  ok,
}: {
  name: string;
  enabled: boolean;
  detail: string;
  ok: boolean | null;
}) {
  const Icon = ok === null ? HelpCircle : ok ? CheckCircle2 : XCircle;
  const color = ok === null ? "text-zinc-500" : ok ? "text-emerald-400" : "text-zinc-500";

  return (
    <div className="flex items-center justify-between px-5 py-3">
      <div className="flex items-center gap-2.5">
        <Icon className={`h-4 w-4 ${color}`} />
        <span className="text-sm text-zinc-200">{name}</span>
      </div>
      <span className="text-xs text-zinc-500">{enabled ? detail : "Not configured"}</span>
    </div>
  );
}
