import type { CuratedListSummary } from "../../../types/api";
import type { RunDraft } from "../useRunDraft";

export interface ModeConfigProps {
  draft: RunDraft;
  update: (changes: Partial<RunDraft>) => void;
  curatedLists: CuratedListSummary[] | undefined;
  onGoLists: () => void;
}
