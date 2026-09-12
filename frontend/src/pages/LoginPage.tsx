import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Film, Loader2 } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { useAuthStore } from "../store/authStore";
import type { User } from "../types/api";

type Mode = "login" | "register";

export default function LoginPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);

  // Already logged in (e.g. cookie still valid) and navigated back to /login directly.
  useEffect(() => {
    let cancelled = false;
    api
      .get<User>("/auth/me")
      .then((user) => {
        if (!cancelled) {
          login(user);
          navigate("/runs", { replace: true });
        }
      })
      .catch(() => {
        /* not logged in yet - show the form */
      });
    return () => {
      cancelled = true;
    };
  }, [login, navigate]);

  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "register") {
        await api.post("/auth/register", { username, password, display_name: displayName });
      }
      const user = await api.post<User>("/auth/login", { username, password });
      login(user);
      navigate("/runs", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          mode === "register" && err.status === 401
            ? "An admin account already exists. Please log in instead."
            : err.message,
        );
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-app-bg px-4">
      <div className="w-full max-w-sm rounded-xl border border-app-border bg-app-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-full bg-accent/10 text-accent">
            <Film className="h-6 w-6" />
          </div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-100">CineChain</h1>
          <p className="text-sm text-zinc-500">
            {mode === "login" ? "Sign in to continue your chain" : "Create the admin account"}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {mode === "register" && (
            <Field label="Display name">
              <input
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className={inputClass}
                placeholder="Alice"
              />
            </Field>
          )}

          <Field label="Username">
            <input
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className={inputClass}
              autoComplete="username"
              placeholder="alice"
            />
          </Field>

          <Field label="Password">
            <input
              required
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputClass}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder="••••••••"
            />
          </Field>

          {error && (
            <p className="rounded-md border border-red-900/50 bg-red-950/40 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 flex items-center justify-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {mode === "login" ? "Sign in" : "Create admin account"}
          </button>
        </form>

        <button
          type="button"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
          className="mt-5 w-full text-center text-xs text-zinc-500 transition-colors hover:text-accent"
        >
          {mode === "login"
            ? "Setting up CineChain for the first time? Create the admin account"
            : "Already have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}

const inputClass =
  "w-full rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
      {label}
      {children}
    </label>
  );
}
