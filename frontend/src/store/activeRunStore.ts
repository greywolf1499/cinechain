import { create } from "zustand";
import { persist } from "zustand/middleware";

interface ActiveRunState {
	/** The run the user is currently working in; tools use it for context-awareness. */
	activeRunId: string | null;
	setActiveRun: (runId: string | null) => void;
}

export const useActiveRunStore = create<ActiveRunState>()(
	persist(
		(set) => ({
			activeRunId: null,
			setActiveRun: (activeRunId) => set({ activeRunId }),
		}),
		{ name: "cinechain.activeRun" },
	),
);
