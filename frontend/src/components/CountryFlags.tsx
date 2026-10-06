import { isoToFlagEmoji } from "../lib/countries";
import { countryName } from "../lib/countryNames";

export default function CountryFlags({ codes, max = 2 }: { codes: string[]; max?: number }) {
  const unique = [...new Set(codes)];
  if (!unique.length) return null;
  const visible = unique.slice(0, Math.max(0, max));
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {visible.map((code) => (
        <span key={code} className="whitespace-nowrap">{isoToFlagEmoji(code)} {countryName(code, code)}</span>
      ))}
      {unique.length > visible.length && (
        <span title={unique.slice(visible.length).map((code) => countryName(code, code)).join(", ")}>
          +{unique.length - visible.length}
        </span>
      )}
    </span>
  );
}
