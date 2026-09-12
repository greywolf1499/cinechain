import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle, HelpCircle } from "lucide-react";
import PageHeading from "../components/PageHeading";
import { api } from "../lib/api";
import type { IntegrationStatus } from "../types/api";

export default function SettingsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["integrations", "status"],
    queryFn: () => api.get<IntegrationStatus>("/integrations/status"),
  });

  return (
    <div>
      <PageHeading title="Settings" subtitle="Homelab integrations and system status" />

      <div className="rounded-xl border border-app-border bg-app-surface">
        <div className="border-b border-app-border px-5 py-3 text-sm font-medium text-zinc-300">
          Integrations
        </div>
        <div className="divide-y divide-app-border">
          {isLoading && <div className="px-5 py-4 text-sm text-zinc-500">Loading...</div>}
          {data && (
            <>
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
            </>
          )}
        </div>
      </div>
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
