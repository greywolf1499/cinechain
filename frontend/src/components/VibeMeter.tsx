import type { RulesConfig } from "../types/api";

export default function VibeMeter({ rules }: { rules: RulesConfig }) {
  const state = rules.vibe_state;
  const active = rules.modifiers?.some((modifier) => modifier.key === "vibe_control");
  if (!active || !state) return null;
  const load = state.rolling_load ?? state.load;
  const percent = Math.round(Math.max(0, Math.min(1, load ?? 0)) * 100);
  const tone =
    state.state === "fatigued"
      ? "border-amber-400/40 bg-amber-500/10 text-amber-200"
      : state.state === "recovering"
        ? "border-sky-400/40 bg-sky-500/10 text-sky-200"
        : "border-emerald-400/40 bg-emerald-500/10 text-emerald-200";
  return (
    <section aria-label="Vibe meter" className={`rounded-lg border px-3 py-2 ${tone}`}>
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-semibold">🎚️ {state.state}</span>
        <span>{load == null ? "Load unknown" : `Load ${percent}%`}</span>
      </div>
      <div
        role="meter"
        aria-label="Rolling film load"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={load == null ? undefined : percent}
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-black/30"
      >
        <div className="h-full rounded-full bg-current transition-[width]" style={{ width: `${percent}%` }} />
      </div>
      {state.reason && <p className="mt-1 text-[10px] opacity-80">{state.reason}</p>}
    </section>
  );
}
