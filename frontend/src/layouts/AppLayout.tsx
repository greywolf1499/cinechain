import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { Film, Wrench, BookUser, ListChecks, Users, Settings, LogOut, Menu, X } from "lucide-react";
import { useAuthStore } from "../store/authStore";
import { api } from "../lib/api";
import { cn } from "../lib/cn";

const NAV_ITEMS = [
  { to: "/runs", label: "Runs", icon: Film },
  { to: "/tools", label: "Tools", icon: Wrench },
  { to: "/passport", label: "Passport", icon: BookUser },
  { to: "/lists", label: "Lists", icon: ListChecks },
  { to: "/curators", label: "Curators", icon: Users },
  { to: "/settings", label: "Settings", icon: Settings },
];

export default function AppLayout() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);

  async function handleLogout() {
    try {
      await api.post("/auth/logout");
    } finally {
      logout();
      navigate("/login", { replace: true });
    }
  }

  return (
    <div className="min-h-screen w-full max-w-full min-w-0 bg-app-bg">
      <header className="sticky top-0 z-20 border-b border-app-border bg-app-bg/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
          <div className="flex items-center gap-2 font-semibold tracking-tight text-accent">
            <Film className="h-5 w-5" />
            <span>CineChain</span>
          </div>

          <nav className="hidden flex-1 items-center gap-1 sm:flex">
            {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-app-surface text-accent"
                      : "text-zinc-400 hover:bg-app-surface hover:text-zinc-100",
                  )
                }
              >
                <Icon className="h-4 w-4" />
                {label}
              </NavLink>
            ))}
          </nav>

          <div className="hidden items-center gap-3 text-sm sm:flex">
            <span className="text-zinc-400">{user?.display_name}</span>
            <button
              type="button"
              onClick={handleLogout}
              className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-zinc-400 transition-colors hover:bg-app-surface hover:text-red-400"
            >
              <LogOut className="h-4 w-4" />
              Logout
            </button>
          </div>

          <button
            type="button"
            onClick={() => setMobileOpen((v) => !v)}
            aria-label="Toggle menu"
            className="ml-auto flex h-9 w-9 items-center justify-center rounded-md text-zinc-300 transition-colors hover:bg-app-surface-hover sm:hidden"
          >
            {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>

        {mobileOpen && (
          <nav className="flex flex-col gap-1 border-t border-app-border px-4 py-3 sm:hidden">
            {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-app-surface text-accent"
                      : "text-zinc-400 hover:bg-app-surface hover:text-zinc-100",
                  )
                }
              >
                <Icon className="h-4 w-4" />
                {label}
              </NavLink>
            ))}
            <div className="mt-2 flex items-center justify-between border-t border-app-border pt-2.5">
              <span className="text-sm text-zinc-400">{user?.display_name}</span>
              <button
                type="button"
                onClick={handleLogout}
                className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-zinc-400 transition-colors hover:bg-app-surface hover:text-red-400"
              >
                <LogOut className="h-4 w-4" />
                Logout
              </button>
            </div>
          </nav>
        )}
      </header>

      <main className="mx-auto w-full max-w-6xl min-w-0 px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}

