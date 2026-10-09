import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { useEngines } from "../../../lib/queries";
import type { TugBalance, TugPlaneMeta } from "../../../types/api";
import type { ModeConfigProps } from "./types";

function parseParameter(raw: string, current: unknown): unknown {
  if (Array.isArray(current)) {
    const numeric = typeof current[0] === "number";
    return raw.split(",").map((item) => (numeric ? Number(item.trim()) : item.trim()));
  }
  if (typeof current === "number") return Number(raw);
  return raw;
}

export default function TugConfig({ draft, update }: ModeConfigProps) {
  const { data: engines } = useEngines();
  const engine = engines?.find((item) => item.game_type === "tug_of_war");
  const planes = (engine?.tug_planes ?? []).filter((plane) => !plane.frozen);
  const plane =
    planes.find((item) => item.id === draft.tugPlaneId) ?? planes.find((item) => item.id === "genre_clusters");
  const traversal =
    plane?.allowed_traversals.find((item) => item.id === draft.tugTraversal) ??
    plane?.allowed_traversals.find((item) => item.id === plane.default_traversal);
  const [balance, setBalance] = useState<TugBalance | null>(null);
  const [balanceError, setBalanceError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const target =
    engine?.rule_fields.find((field) => field.key === "target_lead")?.default ?? 7;

  useEffect(() => {
    if (!plane || !traversal) return;
    let active = true;
    setBalanceError(null);
    void api
      .post<TugBalance>("/engine/tug/balance", {
        plane: plane.id,
        params: { ...plane.defaults, ...draft.tugParams },
        traversal: traversal.id,
      })
      .then((result) => {
        if (active) setBalance(result);
      })
      .catch((error: unknown) => {
        if (active) setBalanceError(error instanceof Error ? error.message : "Balance check failed.");
      });
    return () => {
      active = false;
    };
  }, [draft.tugParams, plane, traversal]);

  function choosePlane(selected: TugPlaneMeta) {
    update({
      tugPlaneId: selected.id,
      tugTraversal: selected.default_traversal,
      tugParams: { ...selected.defaults },
    });
  }

  async function chooseRandom() {
    setLoading(true);
    setBalanceError(null);
    try {
      const result = await api.post<TugBalance & { plane: string }>("/engine/tug/balance", {
        plane: "random",
      });
      const selected = planes.find((item) => item.id === result.plane);
      if (!selected) throw new Error("The server returned an unknown Tug plane.");
      update({
        tugPlaneId: selected.id,
        tugTraversal: result.traversal,
        tugParams: { ...selected.defaults },
      });
      setBalance(result);
    } catch (error) {
      setBalanceError(error instanceof Error ? error.message : "No balanced plane is available.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-lime-400/30 bg-lime-500/5 p-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-zinc-300">Choose the two sides of the rope</span>
        <button
          type="button"
          onClick={() => void chooseRandom()}
          disabled={loading || planes.length === 0}
          className="rounded-md border border-app-border px-2 py-1 text-xs text-zinc-200 hover:border-lime-400 disabled:opacity-50"
        >
          {loading ? "Finding…" : "Random plane"}
        </button>
      </div>
      <div role="radiogroup" aria-label="Tug of War plane" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {planes.map((item) => {
          const active = item.id === plane?.id;
          return (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => choosePlane(item)}
              className={cn(
                "flex flex-col gap-1 rounded-lg border p-2.5 text-left transition-colors",
                active ? "border-lime-400 bg-lime-500/10" : "border-app-border hover:border-zinc-600",
              )}
            >
              <span className="text-sm font-semibold text-zinc-100">{item.label}</span>
              <span className="text-[11px] text-zinc-400">{item.blurb}</span>
              <span className="text-[11px] text-lime-300">
                {item.poles.team_a?.label} vs {item.poles.team_b?.label}
              </span>
            </button>
          );
        })}
      </div>
      {plane && Object.keys(plane.params_schema).length > 0 && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {Object.entries(plane.params_schema).map(([key, schema]) => {
            const current = draft.tugParams[key] ?? plane.defaults[key] ?? schema.default ?? "";
            return (
              <label key={key} className="flex flex-col gap-1 text-[11px] text-zinc-400">
                {key.replaceAll("_", " ")}
                <input
                  type="text"
                  value={Array.isArray(current) ? current.join(", ") : String(current)}
                  onChange={(event) =>
                    update({
                      tugParams: {
                        ...plane.defaults,
                        ...draft.tugParams,
                        [key]: parseParameter(event.target.value, current),
                      },
                    })
                  }
                  className="rounded-md border border-app-border bg-app-surface px-2 py-1.5 text-sm text-zinc-100"
                />
              </label>
            );
          })}
        </div>
      )}
      {plane && (
        <label className="flex flex-col gap-1 text-xs text-zinc-400">
          How films connect
          <select
            value={traversal?.id ?? plane.default_traversal}
            onChange={(event) => update({ tugTraversal: event.target.value })}
            className="rounded-md border border-app-border bg-app-surface px-2 py-2 text-sm text-zinc-100"
          >
            {plane.allowed_traversals.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}{item.graph ? " · linked films" : " · attribute link"}
              </option>
            ))}
          </select>
        </label>
      )}
      <section aria-live="polite" className="rounded-md bg-black/20 p-2 text-xs">
        {balanceError ? (
          <p className="text-rose-300">{balanceError}</p>
        ) : !balance ? (
          <p className="text-zinc-400">Checking cached film balance…</p>
        ) : (
          <>
            <p className={balance.balanced ? "text-emerald-300" : "text-amber-300"}>
              {balance.balanced ? "Balanced" : "Needs a different plane or traversal"} · {balance.eligible} cached films
            </p>
            <p className="mt-1 text-zinc-400">
              Team A {balance.team_a} · Team B {balance.team_b} · Neutral {balance.neutral}
              {balance.bridge_density != null ? ` · Bridges ${(balance.bridge_density * 100).toFixed(1)}%` : ""}
            </p>
            {balance.explanation && <p className="mt-1 text-amber-200">{balance.explanation}</p>}
            {balance.issues.map((issue) => <p key={issue} className="mt-1 text-amber-200">{issue}</p>)}
          </>
        )}
      </section>
      <p className="text-[11px] text-zinc-500">
        You are Team A; the next participant is Team B. First to lead by {target} wins.
      </p>
    </div>
  );
}
