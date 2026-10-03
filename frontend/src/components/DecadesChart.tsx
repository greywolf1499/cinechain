/** Bars of watched films per decade: plain flex columns, heights proportional to the busiest decade. */
export default function DecadesChart({ distribution }: { distribution: Record<string, number> }) {
  const decades = Object.keys(distribution)
    .map((label) => parseInt(label, 10))
    .filter((decade) => !Number.isNaN(decade));
  if (decades.length === 0) return <p className="text-sm text-zinc-500">No watched films with a release year yet.</p>;

  // Include empty decades in between so gaps in a watch history are visible.
  const first = Math.min(...decades);
  const last = Math.max(...decades);
  const columns: { label: string; count: number }[] = [];
  for (let decade = first; decade <= last; decade += 10) {
    columns.push({ label: `${decade}s`, count: distribution[`${decade}s`] ?? 0 });
  }
  const max = Math.max(...columns.map((c) => c.count));

  return (
    <div
      role="img"
      aria-label={`Films watched per decade, from the ${columns[0].label} to the ${columns[columns.length - 1].label}`}
      className="flex h-52 items-end gap-1.5 rounded-xl border border-app-border bg-app-surface px-3 pb-2 pt-3 sm:gap-2 sm:px-4"
    >
      {columns.map(({ label, count }) => (
        <div key={label} className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1" title={`${label}: ${count} ${count === 1 ? "film" : "films"}`}>
          <span className="text-[10px] font-medium text-zinc-400">{count > 0 ? count : ""}</span>
          <div className="flex w-full flex-1 items-end">
            <div
              className="w-full rounded-t bg-accent transition-[height] duration-500"
              style={{ height: count > 0 ? `${Math.max(3, (count / max) * 100)}%` : "2px", opacity: count > 0 ? 1 : 0.2 }}
            />
          </div>
          <span className="text-[10px] text-zinc-500">
            <span className="hidden sm:inline">{label}</span>
            <span className="sm:hidden">&rsquo;{label.slice(2, 4)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}
