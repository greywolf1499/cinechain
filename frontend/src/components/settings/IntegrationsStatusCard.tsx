import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, HelpCircle, Loader2, Search, XCircle } from "lucide-react";
import Toast, { type ToastState } from "../Toast";
import { SettingsCard, inputClass } from "./shared";
import { ApiError, api } from "../../lib/api";
import { useIntegrationStatus } from "../../lib/queries";
import { useAuthStore } from "../../store/authStore";
import type {
  ConnectivityTestResult,
  IntegrationConfig,
  JellyfinTestLookupResult,
  RequestClientStatus,
} from "../../types/api";

/** Jellyfin/Radarr/Seerr reachability rows, plus (admins) the TMDB/OMDb/Jellyfin credential editor. */
export default function IntegrationsStatusCard() {
  const currentUser = useAuthStore((s) => s.user);
  const { data, isLoading } = useIntegrationStatus();

  return (
    <SettingsCard title="Connections">
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
            detail={requestServiceDetail(data.radarr)}
            ok={data.radarr.enabled ? data.radarr.reachable : null}
          />
          <IntegrationRow
            name="Seerr"
            enabled={data.seerr.enabled}
            detail={requestServiceDetail(data.seerr)}
            ok={data.seerr.enabled ? data.seerr.reachable : null}
          />
        </div>
      )}
      {currentUser?.is_admin && <IntegrationSettingsEditor />}
    </SettingsCard>
  );
}

function requestServiceDetail(status: RequestClientStatus): string {
  if (!status.enabled) return "Not configured";
  if (!status.reachable) return "Configured but unreachable";
  return `Reachable${status.version ? ` (v${status.version})` : ""}`;
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
  const [omdbKey, setOmdbKey] = useState("");
  const [omdbResult, setOmdbResult] = useState<ConnectivityTestResult | null>(null);
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
  const testOmdb = useMutation({
    mutationFn: (omdb_api_key: string) =>
      api.post<ConnectivityTestResult>("/settings/integrations/test-omdb", { omdb_api_key }),
  });
  const testJellyfin = useMutation({
    mutationFn: (payload: { jellyfin_url: string; jellyfin_api_key?: string }) =>
      api.post<ConnectivityTestResult>("/settings/integrations/test-jellyfin", payload),
  });
  const saveIntegrations = useMutation({
    mutationFn: (
      payload: Partial<
        Record<"tmdb_api_key" | "jellyfin_url" | "jellyfin_api_key" | "omdb_api_key", string>
      >,
    ) => api.patch<IntegrationConfig>("/settings/integrations", payload),
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

  async function handleOmdbTestAndSave() {
    const key = omdbKey.trim();
    if (!key) {
      setToast({ type: "error", message: "Enter an OMDb API key first." });
      return;
    }
    try {
      const result = await testOmdb.mutateAsync(key);
      setOmdbResult(result);
      if (!result.reachable) {
        setToast({ type: "error", message: result.detail ?? "OMDb connection failed." });
        return;
      }
      await saveIntegrations.mutateAsync({ omdb_api_key: key });
      setOmdbKey("");
      setToast({ type: "success", message: "OMDb connected and saved." });
    } catch (err) {
      setToast({
        type: "error",
        message: err instanceof ApiError ? err.message : "OMDb test failed.",
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
        <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
          OMDb API Key <span className="normal-case text-zinc-600">(IMDb / Rotten Tomatoes ratings)</span>
        </p>
        <p className="mb-2 text-xs text-zinc-500">
          {config?.omdb_configured ? `Currently set: ${config.omdb_api_key_masked}` : "Not configured"}
        </p>
        <div className="flex flex-wrap gap-2">
          <input
            type="password"
            value={omdbKey}
            onChange={(e) => setOmdbKey(e.target.value)}
            placeholder="Enter an OMDb API key..."
            className={`${inputClass} min-w-[240px] flex-1`}
          />
          <button
            type="button"
            onClick={handleOmdbTestAndSave}
            disabled={testOmdb.isPending || saveIntegrations.isPending}
            className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            {(testOmdb.isPending || saveIntegrations.isPending) && (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            )}
            Test &amp; Save
          </button>
        </div>
        {omdbResult && (
          <p
            className={`mt-1.5 flex items-center gap-1.5 text-xs ${omdbResult.reachable ? "text-emerald-400" : "text-red-400"}`}
          >
            {omdbResult.reachable ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <XCircle className="h-3.5 w-3.5" />
            )}
            {omdbResult.reachable ? "Connected" : omdbResult.detail ?? "Failed"}
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

        <JellyfinLookupInspector />
      </div>

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

function JellyfinLookupInspector() {
  const [query, setQuery] = useState("");
  const testLookup = useMutation({
    mutationFn: (q: string) =>
      api.post<JellyfinTestLookupResult>("/integrations/jellyfin/test-lookup", { query: q }),
  });

  function handleTest() {
    const trimmed = query.trim();
    if (!trimmed) return;
    testLookup.mutate(trimmed);
  }

  return (
    <div className="mt-4 rounded-md border border-app-border bg-app-bg p-3">
      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
        <Search className="h-3.5 w-3.5" /> Jellyfin Lookup Inspector
      </p>
      <p className="mb-2 text-xs text-zinc-600">
        Enter a movie title or TMDB id to see exactly what Jellyfin's API returns for it.
      </p>
      <div className="flex flex-wrap gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. The Matrix, or 603"
          className={`${inputClass} min-w-[200px] flex-1`}
        />
        <button
          type="button"
          onClick={handleTest}
          disabled={testLookup.isPending}
          className="flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
        >
          {testLookup.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Test Lookup
        </button>
      </div>

      {testLookup.data && (
        <div className="mt-2.5 flex flex-col gap-1.5">
          <p className="text-[11px] text-zinc-500">
            Query type: <span className="text-zinc-300">{testLookup.data.query_type}</span>
            {!testLookup.data.enabled && " - Jellyfin is not configured"}
          </p>
          {testLookup.data.matches.length === 0 && testLookup.data.enabled && (
            <p className="text-xs text-amber-400">No matches found.</p>
          )}
          {testLookup.data.matches.map((match) => (
            <pre
              key={match.item_id ?? match.name}
              className="overflow-x-auto rounded-md bg-app-surface-hover p-2 text-[11px] text-zinc-300"
            >
              {JSON.stringify(match, null, 2)}
            </pre>
          ))}
        </div>
      )}
    </div>
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
