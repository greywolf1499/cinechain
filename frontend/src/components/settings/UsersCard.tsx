import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { SettingsCard, inputClass } from "./shared";
import { ApiError, api } from "../../lib/api";
import { queryKeys, useUsers } from "../../lib/queries";
import { useAuthStore } from "../../store/authStore";
import type { User } from "../../types/api";

export default function UsersCard() {
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
