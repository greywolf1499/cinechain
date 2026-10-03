import { useState } from "react";
import { Check } from "lucide-react";
import { SettingsCard } from "./shared";
import { cn } from "../../lib/cn";
import { THEMES, applyTheme, getStoredThemeId } from "../../lib/theme";

/** Per-device accent color (stored in this browser only). */
export default function ThemeCard() {
  const [themeId, setThemeId] = useState(getStoredThemeId);

  return (
    <SettingsCard title="UI Theme">
      <div className="flex flex-col gap-3 px-5 py-4">
        <p className="text-xs text-zinc-500">
          Accent color for this device. CineChain is dark-mode only; each household member can pick their own.
        </p>
        <div className="flex flex-wrap gap-2">
          {THEMES.map((theme) => {
            const selected = theme.id === themeId;
            return (
              <button
                key={theme.id}
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  applyTheme(theme.id);
                  setThemeId(theme.id);
                }}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium transition-colors",
                  selected ? "border-accent text-zinc-100" : "border-app-border text-zinc-400 hover:bg-app-surface-hover",
                )}
              >
                <span
                  className="flex h-4 w-4 items-center justify-center rounded-full"
                  style={{ backgroundColor: theme.accent }}
                >
                  {selected && <Check className="h-3 w-3 text-zinc-950" />}
                </span>
                {theme.label}
              </button>
            );
          })}
        </div>
      </div>
    </SettingsCard>
  );
}
