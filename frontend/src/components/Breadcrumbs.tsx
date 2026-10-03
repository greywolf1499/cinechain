import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

export interface Crumb {
  label: string;
  to?: string;
}

export default function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-4 flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
      {items.map((item, index) => (
        <span key={`${item.label}-${index}`} className="flex min-w-0 items-center gap-1.5">
          {index > 0 && <ChevronRight className="h-3 w-3 shrink-0" />}
          {item.to ? (
            <Link to={item.to} className="transition-colors hover:text-zinc-200">
              {item.label}
            </Link>
          ) : (
            <span aria-current="page" className="min-w-0 truncate text-zinc-300">
              {item.label}
            </span>
          )}
        </span>
      ))}
    </nav>
  );
}
