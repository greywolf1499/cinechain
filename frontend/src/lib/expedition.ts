import type { Expedition } from "../types/api";
import { countryName } from "./countryNames";

export const REGIONAL_DEEP_DIVE = "regional_deep_dive";

/** Countries with a deep canon presence, as `[ISO 3166-1 alpha-2, name]`; the dropdown sorts by name. */
const COUNTRY_CODES = [
  "AR", "AT", "AU", "BE", "BR", "CA", "CH", "CL", "CN", "CO", "CU", "CZ", "DE", "DK", "EG", "ES",
  "FI", "FR", "GB", "GR", "HK", "HU", "IE", "IL", "IN", "IR", "IS", "IT", "JP", "KR", "MX", "NL",
  "NO", "NZ", "PH", "PL", "PT", "RO", "RU", "SE", "SN", "TH", "TR", "TW", "UA", "US", "VN", "ZA",
];

export const EXPEDITION_COUNTRIES = COUNTRY_CODES.map((code) => ({
  code,
  name: countryName(code),
})).sort((a, b) => a.name.localeCompare(b.name));

/** "Japan · 1970s", "Japan" or "1970s": what the slice is called in the UI. */
export function sliceLabel(expedition: Pick<Expedition, "country" | "country_name" | "decade">): string {
  const country = expedition.country ? (expedition.country_name ?? expedition.country) : null;
  const decade = expedition.decade !== null ? `${expedition.decade}s` : null;
  return [country, decade].filter(Boolean).join(" · ");
}
