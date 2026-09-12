import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { Film, GitBranch, BookUser, Settings, LogOut } from "lucide-react";
import { useAuthStore } from "../store/authStore";
import { api } from "../lib/api";
import { cn } from "../lib/cn";

const NAV_ITEMS = [
  { to: "/runs", label: "Runs", icon: Film },
  { to: "/bridge", label: "Bridge Solver", icon: GitBranch },
  { to: "/passport", label: "Passport", icon: BookUser },
  { to: "/settings", label: "Settings", icon: Settings },
];

export default function AppLayout() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();

  async function handleLogout() {
    try {
      await api.post("/auth/logout");
    } finally {
      logout();
      navigate("/login", { replace: true });
    }
  }

  return (
    <div className="min-h-screen bg-app-bg">
      <header className="sticky top-0 z-10 border-b border-app-border bg-app-bg/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
          <div className="flex items-center gap-2 font-semibold tracking-tight text-accent">
            <Film className="h-5 w-5" />
            <span>CineChain</span>
          </div>

          <nav className="flex flex-1 items-center gap-1">
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

          <div className="flex items-center gap-3 text-sm">
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
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
