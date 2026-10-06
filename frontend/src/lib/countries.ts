export function isoToFlagEmoji(iso: string): string {
	if (iso.length !== 2) return iso;
	return iso
		.toUpperCase()
		.replace(/./g, (char) => String.fromCodePoint(127397 + char.charCodeAt(0)));
}

/** Compatibility with the legacy string contract for one release. */
export function parseOriginCountries(raw: string | null | undefined): string[] {
	if (!raw) return [];
	let values: unknown[];
	try {
		const parsed = JSON.parse(raw);
		values = Array.isArray(parsed) ? parsed : [parsed];
	} catch {
		values = raw.split(/[,;|/\s]+/);
	}
	const legacy: Record<string, string> = { SU: "RU", YU: "RS", CS: "RS", XC: "CZ", DD: "DE", AN: "CW" };
	return [...new Set(values.filter((code): code is string => typeof code === "string")
		.map((code) => code.trim().toUpperCase())
		.filter((code) => /^[A-Z]{2}$/.test(code))
		.map((code) => legacy[code] ?? code))];
}
