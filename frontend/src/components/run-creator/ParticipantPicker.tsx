import type { UserSummary } from "../../types/api";

export default function ParticipantPicker({
  users,
  selectedIds,
  owner,
  showTeams,
  onToggle,
}: {
  users: UserSummary[] | undefined;
  selectedIds: string[];
  owner: UserSummary | null | undefined;
  showTeams: boolean;
  onToggle: (id: string) => void;
}) {
  const participants = selectedIds
    .map((id) => users?.find((user) => user.id === id))
    .filter((user): user is UserSummary => !!user);

  return (
    <section className="flex flex-col gap-2" aria-labelledby="participant-heading">
      <h3 id="participant-heading" className="text-xs font-medium text-zinc-400">Participants</h3>
      {showTeams && (
        <p className="text-[11px] text-amber-200">
          {owner?.display_name ?? "You"} → Team A
          {participants[0] ? ` · ${participants[0].display_name} → Team B` : " · first partner you add → Team B"}
          {participants.length > 1 ? ` · ${participants.length - 1} more partner${participants.length > 2 ? "s" : ""}` : ""}
        </p>
      )}
      <div className="flex flex-wrap gap-2 rounded-md border border-app-border bg-app-bg p-2">
        {users?.filter((user) => user.id !== owner?.id).map((user) => {
          const selected = selectedIds.includes(user.id);
          return (
            <button
              key={user.id}
              type="button"
              aria-pressed={selected}
              onClick={() => onToggle(user.id)}
              className={`rounded-full border px-3 py-1.5 text-xs transition-colors ${
                selected
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-app-border text-zinc-400 hover:border-zinc-500 hover:text-zinc-100"
              }`}
            >
              {user.display_name}
            </button>
          );
        })}
        {users?.length === 1 && <span className="px-2 py-1 text-xs text-zinc-500">No other users yet.</span>}
      </div>
    </section>
  );
}
