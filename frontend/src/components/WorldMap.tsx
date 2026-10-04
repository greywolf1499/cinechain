import { memo, useCallback, useMemo, useRef, useState, type PointerEvent } from "react";
import { WORLD_MAP, WORLD_MAP_VIEWBOX } from "../lib/worldMapData";
import { countryName } from "../lib/countryNames";
import { countryCenter } from "../lib/mapGeometry";
import type { PassportCountry } from "../types/api";

/** 1 film -> faint, 10+ films -> solid. */
const FULL_COLOR_AT = 10;
export function countryOpacity(count: number): number {
  return 0.3 + 0.7 * Math.min(1, (count - 1) / (FULL_COLOR_AT - 1));
}

interface TooltipState {
  code: string;
  name: string;
  x: number;
  y: number;
}

/** One stop of a marathon: its 1-based step number and the country it was in. */
export interface RouteStop {
  step: number;
  code: string;
}

const PIN_RADIUS = 8;

/** The route as dashed legs plus one numbered pin per country (first visit's number, "+n" for returns). */
function RouteOverlay({ route }: { route: RouteStop[] }) {
  const located = route.flatMap((stop) => {
    const point = countryCenter(stop.code);
    return point ? [{ ...stop, point }] : [];
  });
  const pins = new Map<string, { point: { x: number; y: number }; steps: number[] }>();
  for (const stop of located) {
    const pin = pins.get(stop.code) ?? { point: stop.point, steps: [] };
    pin.steps.push(stop.step);
    pins.set(stop.code, pin);
  }
  return (
    <g pointerEvents="none">
      {located.slice(1).map((stop, i) => {
        const from = located[i];
        if (from.code === stop.code) return null;
        return (
          <line
            key={`${from.step}-${stop.step}`}
            x1={from.point.x}
            y1={from.point.y}
            x2={stop.point.x}
            y2={stop.point.y}
            stroke="var(--color-accent)"
            strokeWidth={1.4}
            strokeDasharray="4 3"
            strokeOpacity={0.9}
          />
        );
      })}
      {[...pins.entries()].map(([code, pin]) => (
        <g key={code} transform={`translate(${pin.point.x} ${pin.point.y})`}>
          <title>{`${countryName(code)}: step ${pin.steps.join(", ")}`}</title>
          <circle r={PIN_RADIUS} fill="var(--color-accent)" stroke="#09090b" strokeWidth={1.5} />
          <text
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={pin.steps[0] > 9 ? 7 : 9}
            fontWeight={700}
            fill="#09090b"
          >
            {pin.steps[0]}
          </text>
          {pin.steps.length > 1 && (
            <text x={PIN_RADIUS} y={-PIN_RADIUS + 2} fontSize={7} fontWeight={700} fill="#fafafa">
              +{pin.steps.length - 1}
            </text>
          )}
        </g>
      ))}
    </g>
  );
}

/** The static SVG layer. Memoized so tooltip movement never re-renders ~250 large paths. */
const MapPaths = memo(function MapPaths({ counts }: { counts: ReadonlyMap<string, number> }) {
  return (
    <>
      {WORLD_MAP.map((country) => {
        const count = counts.get(country.id) ?? 0;
        return (
          <path
            key={country.id}
            id={country.id}
            d={country.d}
            data-visited={count > 0 || undefined}
            style={
              count > 0
                ? { fill: "var(--color-accent)", fillOpacity: countryOpacity(count) }
                : { fill: "var(--color-app-surface-hover)" }
            }
          />
        );
      })}
    </>
  );
});

/** Global Cinema Scratch-Off: a plain inline SVG whose `<path id>`s are ISO 3166-1
 * alpha-2 codes, shaded by how many watched films come from each country. */
export default function WorldMap({
  countries,
  route,
  legend = true,
}: {
  countries: PassportCountry[];
  /** A run's countries in step order: drawn as numbered pins joined by a dashed route. */
  route?: RouteStop[];
  legend?: boolean;
}) {
  const counts = useMemo(() => new Map(countries.map((c) => [c.code, c.count])), [countries]);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  const onPointer = useCallback((event: PointerEvent<SVGSVGElement>) => {
    const target = event.target as SVGElement;
    const container = containerRef.current;
    if (!container || target.tagName !== "path" || !target.id) {
      setTooltip(null);
      return;
    }
    const box = container.getBoundingClientRect();
    setTooltip({
      code: target.id,
      name: WORLD_MAP.find((c) => c.id === target.id)?.name ?? countryName(target.id),
      x: event.clientX - box.left,
      y: event.clientY - box.top,
    });
  }, []);

  const visited = counts.size;
  return (
    <div ref={containerRef} className="relative">
      <svg
        viewBox={WORLD_MAP_VIEWBOX}
        role="img"
        aria-label={`World map: ${visited} ${visited === 1 ? "country" : "countries"} with watched films`}
        onPointerMove={onPointer}
        onPointerDown={onPointer}
        onPointerLeave={() => setTooltip(null)}
        className="h-auto w-full touch-pan-y select-none rounded-xl border border-app-border bg-app-bg stroke-app-border [&_path]:stroke-[0.4] [&_path:hover]:stroke-zinc-200 [&_path:hover]:stroke-[0.8]"
      >
        <MapPaths counts={counts} />
        {route && route.length > 0 && <RouteOverlay route={route} />}
      </svg>

      {tooltip && (
        <div
          role="tooltip"
          style={{ left: tooltip.x, top: tooltip.y }}
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-[130%] whitespace-nowrap rounded-md border border-app-border bg-app-surface px-2.5 py-1.5 text-xs text-zinc-100 shadow-xl shadow-black/50"
        >
          <span className="font-medium">{tooltip.name}</span>
          <span className="ml-1.5 text-zinc-400">
            {(() => {
              const count = counts.get(tooltip.code) ?? 0;
              return count > 0 ? `${count} ${count === 1 ? "film" : "films"}` : "not visited yet";
            })()}
          </span>
        </div>
      )}

      {legend && (
      <div className="mt-2 flex items-center gap-2 text-[11px] text-zinc-500" aria-hidden="true">
        <span>Fewer</span>
        {[1, 3, 5, 7, 10].map((n) => (
          <span
            key={n}
            className="h-2.5 w-6 rounded-sm"
            style={{ backgroundColor: "var(--color-accent)", opacity: countryOpacity(n) }}
          />
        ))}
        <span>10+ films</span>
        <span className="ml-3 h-2.5 w-6 rounded-sm border border-app-border bg-app-surface-hover" />
        <span>Not visited</span>
      </div>
      )}
    </div>
  );
}
