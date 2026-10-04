import { cn } from "../lib/cn";
import { ROLE_STYLES, roleBadgeText } from "../lib/crewRoles";
import type { CraftRole } from "../types/api";

/** "🎼 Composer: Hans Zimmer" - the role a connecting person held on a link. */
export default function RoleBadge({
  role,
  name,
  fromRole,
  className,
}: {
  role: CraftRole;
  name: string;
  /** Their role on the earlier film, when it differs (a director who also acts). */
  fromRole?: CraftRole | null;
  className?: string;
}) {
  return (
    <span
      title={roleBadgeText(role, name, fromRole)}
      className={cn(
        "inline-flex max-w-full items-center rounded-full px-2 py-0.5 text-[10px] font-semibold",
        ROLE_STYLES[role].className,
        className,
      )}
    >
      <span className="truncate">{roleBadgeText(role, name, fromRole)}</span>
    </span>
  );
}
