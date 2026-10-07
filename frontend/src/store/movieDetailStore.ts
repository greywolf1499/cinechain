import { create } from "zustand";
import type { ReactNode } from "react";
import type { RunStep } from "../types/api";
import type { ActorClickPayload } from "../components/actorClickTypes";

export interface MovieDetailOptions {
  step?: RunStep;
  runId?: string;
  actions?: ReactNode;
  onActorClick?: (actor: ActorClickPayload) => void;
}

interface MovieDetailState {
  movieId: number | null;
  options: MovieDetailOptions;
  open: (movieId: number, options?: MovieDetailOptions) => void;
  close: () => void;
}

export const useMovieDetail = create<MovieDetailState>((set) => ({
  movieId: null,
  options: {},
  open: (movieId, options = {}) => set({ movieId, options }),
  close: () => set({ movieId: null, options: {} }),
}));
