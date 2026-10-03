export interface ThemeOption {
	id: string;
	label: string;
	accent: string;
	accentStrong: string;
}

export const THEMES: ThemeOption[] = [
	{ id: "amber", label: "Amber", accent: "#d9a441", accentStrong: "#f0bc5e" },
	{ id: "emerald", label: "Emerald", accent: "#34b37e", accentStrong: "#4fd199" },
	{ id: "sky", label: "Sky", accent: "#3b9de0", accentStrong: "#62b5ee" },
	{ id: "rose", label: "Rose", accent: "#e0527a", accentStrong: "#ee7a9a" },
	{ id: "violet", label: "Violet", accent: "#8b6fe0", accentStrong: "#a891ee" },
];

const STORAGE_KEY = "cinechain.theme";

export function getStoredThemeId(): string {
	const stored = localStorage.getItem(STORAGE_KEY);
	return THEMES.some((t) => t.id === stored) ? (stored as string) : THEMES[0].id;
}

/** Tailwind v4 exposes `--color-accent*` as plain CSS variables on :root, so
 * overriding them at runtime recolors every `bg-accent`/`text-accent` utility. */
export function applyTheme(id: string): void {
	const theme = THEMES.find((t) => t.id === id) ?? THEMES[0];
	const root = document.documentElement.style;
	root.setProperty("--color-accent", theme.accent);
	root.setProperty("--color-accent-strong", theme.accentStrong);
	localStorage.setItem(STORAGE_KEY, theme.id);
}
