export default function CreateBlockers({
  blockers,
  onSelect,
}: {
  blockers: string[];
  onSelect: (blocker: string) => void;
}) {
  if (blockers.length === 0) return null;
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-semibold text-amber-300" title={blockers.slice(1).join("\n") || undefined}>
        {blockers[0]}
        {blockers.length > 1 && <span className="ml-1 text-amber-400/80">+{blockers.length - 1} more</span>}
      </p>
      <button
        type="button"
        onClick={() => onSelect(blockers[0])}
        className="mt-1 min-h-8 text-left text-[11px] text-accent underline-offset-2 hover:underline"
      >
        Go to first requirement
      </button>
      {blockers.length > 1 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[11px] text-zinc-500">{blockers.length - 1} more requirements</summary>
          <ul className="mt-1 flex flex-col gap-1">
            {blockers.slice(1).map((blocker) => (
              <li key={blocker}>
                <button type="button" onClick={() => onSelect(blocker)} className="text-left text-[11px] text-amber-200 hover:underline">
                  {blocker}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
