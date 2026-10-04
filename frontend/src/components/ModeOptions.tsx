import { AlertTriangle, ArrowDown, ArrowUp, Calendar, Globe2, Link2, Link2Off, Ruler } from "lucide-react";
import { cn } from "../lib/cn";
import {
  DEFAULT_COOLDOWN,
  MAX_COUNTRY_COOLDOWN,
  availableModifiers,
  effectiveCooldown,
  modifierWarnings,
  supportsModifiers,
} from "../lib/modifiers";
import type { ChronoDirection, RulesConfig, RuntimeStaircase } from "../types/api";

/** Per-mode options: Chrono's direction plus the composable Engine V3 modifiers
 * (shared cast, Chrono, Runtime Staircase, Country Cooldown) that can be layered onto
 * any graph mode, with an alert when the stack is anti-synergistic. Nothing for trackers. */
export default function ModeOptions({
  gameType,
  value,
  onChange,
  capabilities,
}: {
  gameType: string;
  value: RulesConfig;
  onChange: (rules: RulesConfig) => void;
  capabilities?: string[];
}) {
  if (!supportsModifiers(gameType, capabilities)) return null;
  const have = availableModifiers(gameType);
  const requireCast = !!value.require_cast_link;
  const chronoMode = gameType === "chrono_climb";
  const chrono: ChronoDirection | null = value.chrono_direction ?? null;
  const modeDirection: ChronoDirection = value.chrono_direction ?? value.direction ?? "climb";
  const staircase: RuntimeStaircase | null = value.runtime_staircase ?? null;
  const cooldown = effectiveCooldown(gameType, value);
  const defaultCooldown = DEFAULT_COOLDOWN[gameType] ?? 0;
  const warnings = modifierWarnings(gameType, value);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-app-border bg-app-bg/60 p-3">
      {chronoMode && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-zinc-400">Direction</p>
          <div className="grid grid-cols-2 gap-2">
            <ChoiceButton
              active={modeDirection === "climb"}
              onClick={() => onChange({ ...value, chrono_direction: "climb" })}
              icon={<ArrowUp className="h-4 w-4" />}
              title="Chrono Climb"
              detail="Each film is newer than the last"
            />
            <ChoiceButton
              active={modeDirection === "descent"}
              onClick={() => onChange({ ...value, chrono_direction: "descent" })}
              icon={<ArrowDown className="h-4 w-4" />}
              title="Chrono Descent"
              detail="Each film is older than the last"
            />
          </div>
        </div>
      )}

      {have.castLink && (
        <ModifierRow
          icon={
            requireCast ? (
              <Link2 className="h-4 w-4 text-accent" />
            ) : (
              <Link2Off className="h-4 w-4 text-zinc-500" />
            )
          }
          title="Require shared cast (hybrid)"
          detail={
            requireCast
              ? "Films must also share a credited actor - the classic CineChain link on top of this mode's rule."
              : "Off: any film that satisfies the rule can follow. Turn on for purist hybrid play."
          }
          checked={requireCast}
          label="Require shared cast"
          onToggle={() => onChange({ ...value, require_cast_link: !requireCast })}
        />
      )}

      {have.chrono && (
        <ModifierRow
          icon={<Calendar className="h-4 w-4 text-violet-300" />}
          title="Chrono direction"
          detail="Every film must be released after (Climb) or before (Descent) the last."
          checked={chrono !== null}
          label="Chrono direction"
          onToggle={() => onChange({ ...value, chrono_direction: chrono ? null : "climb" })}
        >
          <Segmented
            value={chrono}
            onChange={(next) => onChange({ ...value, chrono_direction: next })}
            options={[
              { value: "climb", label: "Climb", icon: <ArrowUp className="h-3 w-3" /> },
              { value: "descent", label: "Descent", icon: <ArrowDown className="h-3 w-3" /> },
            ]}
          />
        </ModifierRow>
      )}

      <ModifierRow
        icon={<Ruler className="h-4 w-4 text-sky-300" />}
        title="Runtime staircase"
        detail="Every film must run longer (ascending) or shorter (descending) than the last."
        checked={staircase !== null}
        label="Runtime staircase"
        onToggle={() => onChange({ ...value, runtime_staircase: staircase ? null : "ascending" })}
      >
        <Segmented
          value={staircase}
          onChange={(next) => onChange({ ...value, runtime_staircase: next })}
          options={[
            { value: "ascending", label: "Ascending", icon: <ArrowUp className="h-3 w-3" /> },
            { value: "descending", label: "Descending", icon: <ArrowDown className="h-3 w-3" /> },
          ]}
        />
      </ModifierRow>

      <ModifierRow
        icon={<Globe2 className="h-4 w-4 text-teal-300" />}
        title="Country cooldown"
        detail={
          defaultCooldown
            ? `A visited country is locked out for the next N steps. This mode's default is ${defaultCooldown}.`
            : "A country can't be picked again until N steps after it was last visited."
        }
        checked={cooldown > 0}
        label="Country cooldown"
        onToggle={() =>
          onChange({ ...value, country_cooldown: cooldown > 0 ? 0 : Math.max(defaultCooldown, 3) })
        }
      >
        <div className="flex items-center gap-2">
          <input
            type="range"
            min={1}
            max={MAX_COUNTRY_COOLDOWN}
            value={Math.max(cooldown, 1)}
            onChange={(e) => onChange({ ...value, country_cooldown: Number(e.target.value) })}
            aria-label="Cooldown steps"
            className="w-full accent-accent"
          />
          <span className="w-14 shrink-0 text-right text-[11px] tabular-nums text-zinc-300">
            {cooldown} step{cooldown === 1 ? "" : "s"}
          </span>
        </div>
      </ModifierRow>

      {warnings.map((warning) => (
        <div
          key={warning.headline}
          role="alert"
          className={cn(
            "flex items-start gap-2 rounded-md border px-2.5 py-2 text-[11px] leading-relaxed",
            warning.level === "danger"
              ? "border-red-900/60 bg-red-950/30 text-red-300"
              : "border-amber-900/60 bg-amber-950/30 text-amber-300",
          )}
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <div>
            <p className="font-semibold">
              <span aria-hidden>⚠️ </span>
              {warning.headline}
            </p>
            <p className="mt-0.5 opacity-80">Why: {warning.why}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function ModifierRow({
  icon,
  title,
  detail,
  checked,
  label,
  onToggle,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
  checked: boolean;
  label: string;
  onToggle: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-3">
        <span className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 shrink-0">{icon}</span>
          <span className="flex flex-col">
            <span className="text-xs font-medium text-zinc-200">{title}</span>
            <span className="text-[11px] text-zinc-500">{detail}</span>
          </span>
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          aria-label={label}
          onClick={onToggle}
          className={cn(
            "relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors",
            checked ? "bg-accent" : "bg-app-surface-hover",
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 h-4 w-4 rounded-full bg-zinc-100 transition-all",
              checked ? "left-[18px]" : "left-0.5",
            )}
          />
        </button>
      </div>
      {checked && children && <div className="pl-6">{children}</div>}
    </div>
  );
}

function Segmented<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T | null;
  onChange: (next: T) => void;
  options: { value: T; label: string; icon: React.ReactNode }[];
}) {
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            "flex items-center justify-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
            value === option.value
              ? "border-accent bg-accent/10 text-accent"
              : "border-app-border text-zinc-400 hover:border-zinc-600 hover:text-zinc-200",
          )}
        >
          {option.icon}
          {option.label}
        </button>
      ))}
    </div>
  );
}

function ChoiceButton({
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
