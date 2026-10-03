import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ImagePlus, Loader2, Trash2 } from "lucide-react";
import Modal from "./Modal";
import ListCover from "./ListCover";
import type { ToastState } from "./Toast";
import { ApiError, api } from "../lib/api";
import { proxiedImageUrl } from "../lib/images";
import type { CuratedListSummary } from "../types/api";

const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;
const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const inputClass =
  "w-full rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";
const labelClass = "mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500";
const buttonClass =
  "flex items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60";

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

/** Unified customization for any list (preset or custom): slug, emoji badge,
 * badge prefix/color and logo (upload, or pick one of the list's posters). */
export default function EditListModal({
  list,
  onClose,
  onToast,
}: {
  list: CuratedListSummary;
  onClose: () => void;
  onToast: (toast: ToastState) => void;
}) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState(list);
  const [slug, setSlug] = useState(list.slug ?? "");
  const [emoji, setEmoji] = useState(list.badge_emoji ?? "");
  const [prefix, setPrefix] = useState(list.badge_prefix);
  const [color, setColor] = useState(list.badge_color);
  const [busy, setBusy] = useState<"save" | "image" | null>(null);

  const slugInvalid = slug !== "" && !SLUG_RE.test(slug);

  async function applyImage(action: () => Promise<CuratedListSummary>, success: string) {
    setBusy("image");
    try {
      setCurrent(await action());
      await queryClient.invalidateQueries({ queryKey: ["curated"] });
      onToast({ type: "success", message: success });
    } catch (err) {
      onToast({ type: "error", message: errorText(err, "Image update failed.") });
    } finally {
      setBusy(null);
    }
  }

  function onFileChosen(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/") || file.type === "image/svg+xml") {
      onToast({ type: "error", message: "Choose a PNG, JPEG, GIF or WebP image." });
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      onToast({ type: "error", message: "Image must be 2MB or smaller." });
      return;
    }
    void applyImage(() => api.put<CuratedListSummary>(`/curated/lists/${current.id}/image`, file), "Logo uploaded.");
    if (fileRef.current) fileRef.current.value = "";
  }

  async function save() {
    if (slugInvalid) return;
    setBusy("save");
    try {
      await api.patch(`/curated/lists/${current.id}`, {
        slug: slug || undefined,
        badge_emoji: emoji,
        badge_prefix: prefix,
        badge_color: color,
      });
      await queryClient.invalidateQueries({ queryKey: ["curated"] });
      onToast({ type: "success", message: `Saved ${current.title}.` });
      onClose();
    } catch (err) {
      onToast({ type: "error", message: errorText(err, "Save failed.") });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Modal open onClose={onClose} title="Customize list" widthClassName="max-w-lg">
      <div className="flex flex-col gap-4">
        <p className="break-words text-sm font-medium text-zinc-200">{current.title}</p>

        <div>
          <span className={labelClass}>Logo</span>
          <div className="flex flex-wrap items-center gap-3">
            <ListCover list={current} />
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={busy !== null} onClick={() => fileRef.current?.click()} className={buttonClass}>
                {busy === "image" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ImagePlus className="h-3.5 w-3.5" />}
                Upload
              </button>
              {(current.image_url || current.has_custom_image) && (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    applyImage(
                      () => api.patch<CuratedListSummary>(`/curated/lists/${current.id}`, { clear_image: true }),
                      "Logo removed.",
                    )
                  }
                  className={buttonClass}
                >
                  <Trash2 className="h-3.5 w-3.5" /> Remove
                </button>
              )}
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                className="hidden"
                onChange={(e) => onFileChosen(e.target.files?.[0])}
              />
            </div>
          </div>
          {current.preview_posters.length > 0 && (
            <div className="mt-3">
              <p className="mb-1.5 text-[11px] text-zinc-500">Or use one of this list&apos;s posters:</p>
              <div className="flex flex-wrap gap-2">
                {current.preview_posters.map((src) => (
                  <button
                    key={src}
                    type="button"
                    disabled={busy !== null}
                    onClick={() =>
                      applyImage(
                        () => api.patch<CuratedListSummary>(`/curated/lists/${current.id}`, { image_url: src }),
                        "Logo updated.",
                      )
                    }
                    className="overflow-hidden rounded-md border border-app-border transition-opacity hover:opacity-80 disabled:opacity-50"
                  >
                    <img src={proxiedImageUrl(src) ?? undefined} alt="" className="h-16 w-11 object-cover" />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className={labelClass} htmlFor="list-emoji">Emoji badge</label>
            <input id="list-emoji" value={emoji} maxLength={16} onChange={(e) => setEmoji(e.target.value)} placeholder="🏆" className={inputClass} />
          </div>
          <div>
            <label className={labelClass} htmlFor="list-prefix">Badge prefix</label>
            <input id="list-prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)} className={inputClass} />
          </div>
          <div className="sm:col-span-2">
            <label className={labelClass} htmlFor="list-slug">Slug</label>
            <input id="list-slug" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} className={inputClass} />
            {slugInvalid && (
              <p className="mt-1 text-[11px] text-red-400">Use lowercase letters, numbers and single hyphens.</p>
            )}
          </div>
          <div>
            <label className={labelClass} htmlFor="list-color">Badge color</label>
            <input
              id="list-color"
              type="color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
              className="h-10 w-16 rounded-md border border-app-border bg-app-bg"
            />
          </div>
          <div className="flex items-end">
            <span
              className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold"
              style={{ backgroundColor: `${color}26`, color }}
            >
              {emoji || "🏅"} {prefix || "LIST"} #1
            </span>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-app-border pt-3">
          <button type="button" onClick={onClose} className={buttonClass}>
            Cancel
          </button>
          <button
            type="button"
            disabled={busy !== null || slugInvalid}
            onClick={save}
            className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy === "save" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save
          </button>
        </div>
      </div>
    </Modal>
  );
}
