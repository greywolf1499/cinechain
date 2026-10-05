import { Field } from "../shared";
import ActorPicker from "../../ActorPicker";
import type { ModeConfigProps } from "./types";

export function MethodActorConfig({ draft, update }: ModeConfigProps) {
  return (
    <Field label="The actor whose career you'll watch">
      <ActorPicker value={draft.actor} onChange={(actor) => update({ actor })} />
    </Field>
  );
}

export function DirectorConfig({ draft, update }: ModeConfigProps) {
  return (
    <Field label="The director whose filmography you'll work through">
      <ActorPicker
        value={draft.director}
        onChange={(director) => update({ director })}
        department="Directing"
        noun="director"
        accentClass="border-teal-400/40 bg-teal-500/5"
      />
    </Field>
  );
}
