import { cn } from "../lib/cn";

const SIZES = { sm: "h-7 w-7 text-[11px]", md: "h-11 w-11 text-sm" } as const;

/** A player's initials in a coloured disc (accounts have no uploaded avatar). */
export default function PlayerAvatar({
  name,
  size = "md",
  className,
}: {
  name: string;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return (
    <span
      title={name}
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full border-2 font-semibold",
        SIZES[size],
        className,
      )}
    >
      {initials || "?"}
    </span>
  );
}
