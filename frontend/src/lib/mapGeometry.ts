import { WORLD_MAP } from "./worldMapData";

export interface MapPoint {
  x: number;
  y: number;
}

const cache = new Map<string, MapPoint | null>();

/** Where to pin a country: the centre of its largest landmass (so the US pin isn't pulled
 * towards Alaska/Hawaii, nor France's towards Guiana). The path data is absolute M/L/Z only. */
export function countryCenter(code: string): MapPoint | null {
  const key = code.toUpperCase();
  const hit = cache.get(key);
  if (hit !== undefined) return hit;

  const country = WORLD_MAP.find((c) => c.id === key);
  let best: { area: number; point: MapPoint } | null = null;
  if (country) {
    for (const ring of country.d.split("M").filter(Boolean)) {
      const numbers = ring.match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? [];
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      for (let i = 0; i + 1 < numbers.length; i += 2) {
        minX = Math.min(minX, numbers[i]);
        maxX = Math.max(maxX, numbers[i]);
        minY = Math.min(minY, numbers[i + 1]);
        maxY = Math.max(maxY, numbers[i + 1]);
      }
      const area = (maxX - minX) * (maxY - minY);
      if (Number.isFinite(area) && (!best || area > best.area)) {
        best = { area, point: { x: (minX + maxX) / 2, y: (minY + maxY) / 2 } };
      }
    }
  }
  const point = best?.point ?? null;
  cache.set(key, point);
  return point;
}
