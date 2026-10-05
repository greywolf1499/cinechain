import { useState } from "react";
import { useNavigate } from "react-router-dom";
import GameModePicker from "../GameModePicker";
import { MODE_CATEGORIES, gameModeStyle, type ModeCategory } from "../../lib/gameModes";
import type { EngineMeta, RulesConfig } from "../../types/api";

export default function Step1ModeSelect({
  engines,
  gameType,
  rules,
  onSelect,
  onAdvance,
  isAdmin,
}: {
  engines: EngineMeta[] | undefined;
  gameType: string;
  rules: RulesConfig;
  onSelect: (gameType: string) => void;
  onAdvance: (gameType: string) => void;
  isAdmin: boolean;
}) {
  const navigate = useNavigate();
  const [category, setCategory] = useState<ModeCategory | "All">("All");
  const filteredEngines = category === "All"
    ? engines
    : engines?.filter((engine) => gameModeStyle(engine.game_type).category === category);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-base font-semibold text-zinc-100">Choose a game mode</h3>
        <p className="mt-1 text-xs text-zinc-500">Pick the kind of movie challenge you want to play.</p>
      </div>
      <div className="flex flex-wrap gap-2" aria-label="Filter game modes">
        {(["All", ...MODE_CATEGORIES] as const).map((item) => (
          <button
            key={item}
            type="button"
            aria-pressed={category === item}
            onClick={() => setCategory(item)}
            className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
              category === item
                ? "border-accent bg-accent/10 text-accent"
                : "border-app-border text-zinc-400 hover:text-zinc-100"
            }`}
          >
            {item}
          </button>
        ))}
      </div>
      <GameModePicker
        engines={filteredEngines}
        value={gameType}
        rules={rules}
        showModifierDrawer={false}
        onChange={onSelect}
        onAdvance={onAdvance}
        onRulesChange={(mode, nextRules) => {
          if (mode === gameType) {
            onSelect(mode);
            void nextRules;
          }
        }}
      />
      {engines?.find((engine) => engine.game_type === gameType)?.unavailable_reason && (
        <p role="alert" className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          {engines.find((engine) => engine.game_type === gameType)?.unavailable_reason}
          {isAdmin && (
            <button type="button" onClick={() => navigate("/settings/integrations")} className="ml-1 underline">
              Open integration settings
            </button>
          )}
        </p>
      )}
    </div>
  );
}
