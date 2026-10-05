import BracketSeedPicker from "../../BracketSeedPicker";
import type { ModeConfigProps } from "./types";

export default function BracketConfig({ draft, update }: ModeConfigProps) {
  return <BracketSeedPicker films={draft.bracketFilms} onChange={(bracketFilms) => update({ bracketFilms })} />;
}
