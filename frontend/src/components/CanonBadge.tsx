import type { CanonBadge as CanonBadgeType } from "../types/api";

/** Laurel badge for a movie's curated-canon membership(s), e.g. "🏆 SS22 #1". */
function iconFor(badgeLabel: string): string {
  const prefix = badgeLabel.split(" ")[0]?.toUpperCase() ?? "";
  if (prefix.includes("PALME")) return "🌿";
  if (prefix.startsWith("SS")) return "🏆";
  if (prefix.startsWith("LB")) return "🎖️";
  return "🏅";
}

export default function CanonBadge({ badge }: { badge: CanonBadgeType }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold"
      style={{ backgroundColor: `${badge.badge_color}26`, color: badge.badge_color }}
      title={badge.badge_label}
    >
      {iconFor(badge.badge_label)} {badge.badge_label}
    </span>
  );
}

export function CanonBadgeList({ badges }: { badges: CanonBadgeType[] | undefined }) {
  if (!badges || badges.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {badges.map((badge) => (
        <CanonBadge key={badge.badge_label} badge={badge} />
      ))}
    </div>
  );
}
