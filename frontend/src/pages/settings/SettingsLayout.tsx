import { NavLink, Outlet } from "react-router-dom";
import { Cpu, ListTodo, Plug, SlidersHorizontal, type LucideIcon } from "lucide-react";
import PageHeading from "../../components/PageHeading";
import { cn } from "../../lib/cn";
import { useAuthStore } from "../../store/authStore";

interface Category {
  to: string;
  label: string;
  hint: string;
  icon: LucideIcon;
  adminOnly?: boolean;
}

const CATEGORIES: Category[] = [
  { to: "general", label: "General", hint: "Users, theme, cache", icon: SlidersHorizontal },
  { to: "engine", label: "Engine", hint: "Timeouts, rulesets", icon: Cpu, adminOnly: true },
  { to: "integrations", label: "Integrations", hint: "TMDB, Jellyfin, Letterboxd", icon: Plug },
  { to: "tasks", label: "Tasks & Logs", hint: "Background jobs", icon: ListTodo },
];

export default function SettingsLayout() {
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  const categories = CATEGORIES.filter((c) => !c.adminOnly || isAdmin);

  return (
    <div>
      <PageHeading title="Settings" subtitle="Household, engine, integrations and background tasks" />
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        <nav
          aria-label="Settings categories"
          className="flex shrink-0 gap-1 overflow-x-auto md:sticky md:top-4 md:w-56 md:flex-col md:overflow-visible"
        >
          {categories.map(({ to, label, hint, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 transition-colors",
                  isActive
                    ? "bg-app-surface-hover text-zinc-100"
                    : "text-zinc-400 hover:bg-app-surface hover:text-zinc-200",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{label}</span>
                <span className="hidden text-[11px] text-zinc-500 md:block">{hint}</span>
              </span>
            </NavLink>
          ))}
        </nav>
        <div className="min-w-0 flex-1">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
