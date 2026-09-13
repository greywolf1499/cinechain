export function isoToFlagEmoji(iso: string): string {
	if (iso.length !== 2) return iso;
	return iso
		.toUpperCase()
		.replace(/./g, (char) => String.fromCodePoint(127397 + char.charCodeAt(0)));
}

/** `movie_origin_country` is a JSON-encoded array of ISO codes (or null/invalid). */
export function parseOriginCountries(raw: string | null | undefined): string[] {
	if (!raw) return [];
	try {
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed)
			? parsed.filter((c): c is string => typeof c === "string")
			: [];
	} catch {
		return [];
	}
}
