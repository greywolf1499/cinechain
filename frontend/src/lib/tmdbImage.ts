const IMAGE_BASE = "https://image.tmdb.org/t/p";

export function posterUrl(
	path: string | null,
	size: "w92" | "w154" | "w342" | "w500" = "w342",
) {
	if (!path) return null;
	return `${IMAGE_BASE}/${size}${path}`;
}

export function profileUrl(path: string | null, size: "w45" | "w185" = "w45") {
	if (!path) return null;
	return `${IMAGE_BASE}/${size}${path}`;
}
