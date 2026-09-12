import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, HelpCircle, Loader2, XCircle } from "lucide-react";
import PageHeading from "../components/PageHeading";
import { ApiError, api } from "../lib/api";
import { queryKeys, useCacheStats, useUsers } from "../lib/queries";
import { useAuthStore } from "../store/authStore";
import type { IntegrationStatus, User } from "../types/api";

const inputClass =
  "rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";

export default function SettingsPage() {
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
