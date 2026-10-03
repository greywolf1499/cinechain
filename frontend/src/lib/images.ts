/** Letterboxd/curator images are always fetched through the local image-cache
 * proxy (privacy + link-rot protection). Same-origin paths (uploaded list
 * logos, already-proxied URLs) pass through untouched. */
export function proxiedImageUrl(url: string | null | undefined): string | null {
	if (!url) return null;
	if (url.startsWith("/")) return url;
	return `/api/images/proxy?url=${encodeURIComponent(url)}`;
}
