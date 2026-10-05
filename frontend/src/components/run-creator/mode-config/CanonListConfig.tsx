import { Link } from "react-router-dom";
import { FileText } from "lucide-react";
import EmptyState from "../../EmptyState";
import { Field, inputClass } from "../shared";
import type { ModeConfigProps } from "./types";

export function CanonListSelect({ value, onChange, curatedLists, onGoLists, label }: {
  value: string;
  onChange: (id: string) => void;
  curatedLists: ModeConfigProps["curatedLists"];
  onGoLists: () => void;
  label: string;
}) {
  const lists = (curatedLists ?? []).filter((list) => list.is_enabled && list.total_items > 0);
  if (lists.length === 0) {
    return (
      <EmptyState
        icon={FileText}
        title="No synced lists available"
        description="Enable and sync a curated list before setting up this mode."
        action={{ label: "Open Lists", onClick: onGoLists }}
      />
    );
  }
  return (
    <Field label={label}>
      <select value={value} onChange={(event) => onChange(event.target.value)} className={inputClass}>
        <option value="">Choose a list...</option>
        {lists.map((list) => (
          <option key={list.id} value={list.id}>{list.title} ({list.total_items} films)</option>
        ))}
      </select>
      <Link to="/lists" className="text-[11px] text-accent hover:underline">Browse synced lists</Link>
    </Field>
  );
}

export default function CanonListConfig(props: ModeConfigProps) {
  return (
    <CanonListSelect
      value={props.draft.canonListId}
      onChange={(canonListId) => props.update({ canonListId })}
      curatedLists={props.curatedLists}
      onGoLists={props.onGoLists}
      label="Canon list (every film must be on it)"
    />
  );
}
