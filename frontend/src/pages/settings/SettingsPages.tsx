import type { ReactNode } from "react";
import CuratedCanonsCard from "../../components/CuratedCanonsCard";
import { RadarrSettingsCard, SeerrSettingsCard } from "../../components/ArrIntegrationCards";
import EmbeddingsCard from "../../components/settings/EmbeddingsCard";
import CacheCard from "../../components/settings/CacheCard";
import IntegrationsStatusCard from "../../components/settings/IntegrationsStatusCard";
import RulesetsCard from "../../components/settings/RulesetsCard";
import SolverSettingsCard from "../../components/settings/SolverSettingsCard";
import TasksPanel from "../../components/settings/TasksPanel";
import ThemeCard from "../../components/settings/ThemeCard";
import UsersCard from "../../components/settings/UsersCard";
import WatchlistSyncCard from "../../components/settings/WatchlistSyncCard";
import { useAuthStore } from "../../store/authStore";

function Stack({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-5">{children}</div>;
}

function AdminOnly({ children }: { children: ReactNode }) {
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  return isAdmin ? (
    <>{children}</>
  ) : (
    <p className="rounded-xl border border-dashed border-app-border px-5 py-8 text-center text-sm text-zinc-500">
      Only administrators can change these settings.
    </p>
  );
}

export function GeneralSettings() {
  return (
    <Stack>
      <UsersCard />
      <ThemeCard />
      <CacheCard />
    </Stack>
  );
}

export function EngineSettings() {
  return (
    <AdminOnly>
      <Stack>
        <SolverSettingsCard />
        <RulesetsCard />
      </Stack>
    </AdminOnly>
  );
}

export function IntegrationsSettings() {
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  return (
    <Stack>
      <WatchlistSyncCard />
      <IntegrationsStatusCard />
      {isAdmin && <RadarrSettingsCard />}
      {isAdmin && <SeerrSettingsCard />}
      {isAdmin && <EmbeddingsCard />}
      {isAdmin && <CuratedCanonsCard />}
    </Stack>
  );
}

export function TasksSettings() {
  return <TasksPanel />;
}
