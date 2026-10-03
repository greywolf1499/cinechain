import { Users } from "lucide-react";
import { cn } from "../lib/cn";
import { proxiedImageUrl } from "../lib/images";

const SIZES = { sm: "h-8 w-8", md: "h-12 w-12", lg: "h-24 w-24" } as const;

/** Curator logo/avatar, always served through the local image proxy. */
export default function CuratorAvatar({
  avatarUrl,
  size = "md",
  className,
}: {
  avatarUrl: string | null | undefined;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const src = proxiedImageUrl(avatarUrl);
  return src ? (
    <img
      src={src}
      alt=""
      loading="lazy"
      className={cn("shrink-0 rounded-full border border-app-border object-cover", SIZES[size], className)}
    />
  ) : (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500",
        SIZES[size],
        className,
      )}
    >
      <Users className={size === "lg" ? "h-10 w-10" : "h-4 w-4"} />
    </div>
  );
}
