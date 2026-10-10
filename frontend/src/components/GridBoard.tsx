import { useMemo } from "react";
import { cn } from "../lib/cn";
import type { RunDetail } from "../types/api";

function parseCell(cellId: string): { row: number; col: number } | null {
  const [rowText, colText] = cellId.split(":", 2);
  const row = Number(rowText);
  const col = Number(colText);
  if (!Number.isInteger(row) || !Number.isInteger(col)) return null;
  return { row, col };
}

export default function GridBoard({ run }: { run: RunDetail }) {
  const grid = run.rules_config.grid;
  const size = Number(grid?.size ?? run.rules_config.size ?? 5);
  const cells = Array.isArray(grid?.cells) ? grid.cells : [];
  const byId = new Map(cells.map((cell) => [cell.id, cell]));
  const revealed = new Set(run.rules_config.grid_revealed ?? []);
  const claimedBy = useMemo(() => {
    const mapped = new Map<string, number>();
    for (const step of run.steps) {
      if (step.status !== "watched") continue;
      const cell = step.transition_metadata?.grid_cell;
      if (typeof cell === "string" && !mapped.has(cell)) mapped.set(cell, step.movie_id);
    }
    return mapped;
  }, [run.steps]);
  const winningCells = useMemo(() => {
    const claimed = new Set(claimedBy.keys());
    const lines: string[][] = [];
    for (let index = 0; index < size; index += 1) {
      lines.push(
        Array.from({ length: size }, (_, col) => `${index}:${col}`),
        Array.from({ length: size }, (_, row) => `${row}:${index}`),
      );
    }
    lines.push(
      Array.from({ length: size }, (_, index) => `${index}:${index}`),
      Array.from({ length: size }, (_, index) => `${index}:${size - index - 1}`),
    );
    if (run.rules_config.victory === "blackout" && claimed.size === size * size) {
      return claimed;
    }
    if (run.rules_config.victory === "crossing") {
      const queue = [...claimed].filter((cell) => parseCell(cell)?.col === 0);
      const reached = new Set(queue);
      while (queue.length) {
        const current = queue.shift();
        const point = current ? parseCell(current) : null;
        if (!point) continue;
        if (point.col === size - 1) return reached;
        for (const [row, col] of [
          [point.row - 1, point.col],
          [point.row + 1, point.col],
          [point.row, point.col - 1],
          [point.row, point.col + 1],
        ]) {
          const neighbour = `${row}:${col}`;
          if (row >= 0 && row < size && col >= 0 && col < size
            && claimed.has(neighbour) && !reached.has(neighbour)) {
            reached.add(neighbour);
            queue.push(neighbour);
          }
        }
      }
      return new Set<string>();
    }
    return new Set(lines.find((line) => line.every((cell) => claimed.has(cell))) ?? []);
  }, [claimedBy, run.rules_config.victory, size]);

  return (
    <section className="rounded-xl border border-app-border bg-app-surface p-4">
      <div className="mb-3">
        <h2 className="text-sm font-semibold text-zinc-100">Grid Board</h2>
        <p className="text-xs text-zinc-500">
          Claim adjacent cells by logging matching films.
        </p>
      </div>
      {run.status === "completed" && (
        <p role="status" className="mb-3 rounded-lg border border-amber-400/50 bg-amber-400/10 px-3 py-2 text-sm font-semibold text-amber-200">
          ✨ {run.rules_config.victory === "crossing" ? "Crossing complete!" : run.rules_config.victory === "blackout" ? "Board cleared!" : "Bingo!"}
        </p>
      )}
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: `repeat(${size}, minmax(0, 1fr))` }}
      >
        {Array.from({ length: size * size }, (_, index) => {
          const row = Math.floor(index / size);
          const col = index % size;
          const cellId = `${row}:${col}`;
          const cell = byId.get(cellId);
          const claimedMovieId = claimedBy.get(cellId);
          const hidden = run.rules_config.fog && cell?.hidden && !revealed.has(cellId);
          const parsed = parseCell(cellId);
          return (
            <div
              key={cellId}
              className={cn(
                "min-h-20 rounded-lg border p-2 text-xs",
                winningCells.has(cellId)
                  ? "border-amber-300 bg-amber-300/15 shadow-[0_0_12px_rgba(252,211,77,0.2)]"
                  : claimedMovieId
                  ? "border-emerald-500/60 bg-emerald-500/10"
                  : "border-app-border bg-app-bg",
              )}
            >
              <div className="mb-1 flex items-center justify-between text-[10px] text-zinc-500">
                <span>{parsed ? `R${parsed.row + 1} · C${parsed.col + 1}` : cellId}</span>
                {claimedMovieId ? <span className="text-emerald-300">Claimed</span> : null}
              </div>
              {hidden ? (
                <div aria-label="Hidden cell" className="flex h-9 items-center justify-center">
                  <span className="h-5 w-5 rounded-full border-2 border-dashed border-zinc-600 bg-zinc-800/60" />
                </div>
              ) : (
                <>
                  <p className="font-medium text-zinc-200">{cell?.emoji ? `${cell.emoji} ` : ""}{cell?.label ?? "—"}</p>
                  {claimedMovieId ? (
                    <p className="mt-1 text-[11px] text-zinc-400">Movie #{claimedMovieId}</p>
                  ) : null}
                </>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
