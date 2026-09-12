import { create } from "zustand";
import type { User } from "../types/api";

interface AuthState {
	user: User | null;
	isAuthenticated: boolean;
	setUser: (user: User | null) => void;
	login: (user: User) => void;
	logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
	user: null,
	isAuthenticated: false,
	setUser: (user) => set({ user, isAuthenticated: user !== null }),
	login: (user) => set({ user, isAuthenticated: true }),
	logout: () => set({ user: null, isAuthenticated: false }),
}));
