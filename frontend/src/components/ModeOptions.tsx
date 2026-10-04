import { ArrowDown, ArrowUp, Link2, Link2Off } from "lucide-react";
import { cn } from "../lib/cn";
import { STANDALONE_MODES } from "../lib/gameModes";
import type { RulesConfig } from "../types/api";

/** Per-mode options for the standalone modes: Chrono's direction and the optional
 * "Require shared cast" hybrid modifier. Renders nothing for other modes. */
export default function ModeOptions({
  gameType,
  value,
  onChange,
}: {
  gameType: string;
  value: RulesConfig;
  onChange: (rules: RulesConfig) => void;
}) {
  if (!STANDALONE_MODES.has(gameType)) return null;
  const requireCast = !!value.require_cast_link;
  const direction = value.direction ?? "climb";

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-app-border bg-app-bg/60 p-3">
      {gameType === "chrono_climb" && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-zinc-400">Direction</p>
          <div className="grid grid-cols-2 gap-2">
            <DirectionButton
              active={direction === "climb"}
              onClick={() => onChange({ ...value, direction: "climb" })}
              icon={<ArrowUp className="h-4 w-4" />}
              title="Chrono Climb"
              detail="Each film is newer than the last"
            />
            <DirectionButton
              active={direction === "descent"}
              onClick={() => onChange({ ...value, direction: "descent" })}
              icon={<ArrowDown className="h-4 w-4" />}
              title="Chrono Descent"
              detail="Each film is older than the last"
            />
          </div>
        </div>
      )}

      <label className="flex cursor-pointer items-start justify-between gap-3">
        <span className="flex min-w-0 items-start gap-2">
          {requireCast ? (
            <Link2 className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
          ) : (
            <Link2Off className="mt-0.5 h-4 w-4 shrink-0 text-zinc-500" />
          )}
          <span className="flex flex-col">
            <span className="text-xs font-medium text-zinc-200">Require shared cast (hybrid)</span>
            <span className="text-[11px] text-zinc-500">
              {requireCast
                ? "Films must also share a credited actor - the classic CineChain link on top of this mode's rule."
                : "Off: any film that satisfies the rule can follow. Turn on for purist hybrid play."}
            </span>
          </span>
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={requireCast}
          aria-label="Require shared cast"
          onClick={() => onChange({ ...value, require_cast_link: !requireCast })}
          className={cn(
            "relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors",
            requireCast ? "bg-accent" : "bg-app-surface-hover",
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 h-4 w-4 rounded-full bg-zinc-100 transition-all",
              requireCast ? "left-[18px]" : "left-0.5",
            )}
          />
        </button>
      </label>
    </div>
  );
}

function DirectionButton({
  active,
  onClick,
  icon,
  title,
  detail,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  title: string;
  detail: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors",
        active
          ? "border-violet-400 bg-violet-500/10 text-violet-200"
          : "border-app-border text-zinc-400 hover:border-zinc-600 hover:text-zinc-200",
      )}
    >
      {icon}
      <span className="flex flex-col">
        <span className="text-xs font-semibold">{title}</span>
        <span className="text-[10px] opacity-70">{detail}</span>
      </span>
    </button>
  );
}
