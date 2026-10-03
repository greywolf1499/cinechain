import { Search } from "lucide-react";
import type { ReactNode } from "react";

const fieldClass =
  "rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-200 focus:border-accent focus:outline-none";

export function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2">
      <Search className="h-4 w-4 shrink-0 text-zinc-500" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
      />
    </div>
  );
}

export function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}): ReactNode {
  return (
    <label className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className={`${fieldClass} normal-case tracking-normal`}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
