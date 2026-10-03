import { ChevronLeft, ChevronRight } from "lucide-react";

export default function Pagination({
  page,
  pages,
  total,
  noun,
  onPageChange,
}: {
  page: number;
  pages: number;
  total: number;
  noun: string;
  onPageChange: (page: number) => void;
}) {
  const buttonClass =
    "flex items-center gap-1 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-zinc-500">
      <span>
        {total.toLocaleString()} {noun}
      </span>
      <div className="flex items-center gap-2">
        <button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)} className={buttonClass}>
          <ChevronLeft className="h-3.5 w-3.5" /> Prev
        </button>
        <span className="text-zinc-400">
          Page {page} of {pages}
        </span>
        <button
          type="button"
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
          className={buttonClass}
        >
          Next <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
