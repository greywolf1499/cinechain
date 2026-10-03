import { Film } from "lucide-react";
import { proxiedImageUrl } from "../lib/images";
import type { CuratedListSummary } from "../types/api";

/** A list's logo when one is chosen/uploaded, else a fan of its preview posters. */
export default function ListCover({ list }: { list: CuratedListSummary }) {
  const logo = proxiedImageUrl(list.image_url);
  if (logo) {
    return (
      <img
        src={logo}
        alt=""
        loading="lazy"
        className="h-20 w-14 shrink-0 rounded-md border border-app-border object-cover"
      />
    );
  }
  const posters = list.preview_posters.slice(0, 4);
  if (posters.length === 0) {
    return (
      <div className="flex h-20 w-14 shrink-0 items-center justify-center rounded-md border border-app-border bg-app-surface-hover text-zinc-600">
        <Film className="h-5 w-5" />
      </div>
    );
  }
  return (
    <div className="flex h-20 shrink-0 -space-x-6 pr-6">
      {posters.map((src) => (
        <img
          key={src}
          src={proxiedImageUrl(src) ?? undefined}
          alt=""
          loading="lazy"
          className="h-20 w-14 rounded-sm border border-app-border object-cover shadow-md shadow-black/40"
        />
      ))}
    </div>
  );
}
