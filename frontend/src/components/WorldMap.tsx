import { memo, useCallback, useMemo, useRef, useState, type PointerEvent } from "react";
import { WORLD_MAP, WORLD_MAP_VIEWBOX } from "../lib/worldMapData";
import { countryName } from "../lib/countryNames";
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
export default function WorldMap({ countries }: { countries: PassportCountry[] }) {
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
    </div>
  );
}
