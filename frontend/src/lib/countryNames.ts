const regionNames =
	typeof Intl !== "undefined" && "DisplayNames" in Intl
		? new Intl.DisplayNames(["en"], { type: "region" })
		: null;

/** English country name for an ISO 3166-1 alpha-2 code (falls back to the code). */
export function countryName(code: string, fallback?: string): string {
	try {
		return regionNames?.of(code.toUpperCase()) ?? fallback ?? code;
	} catch {
		return fallback ?? code;
	}
}
