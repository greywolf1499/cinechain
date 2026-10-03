export interface SseEvent {
	event: string;
	data: unknown;
}

/** Parses an SSE-formatted body that has already been fully received; the
 * curated endpoints are POST, which native EventSource can't issue. */
export function parseSseEvents(text: string): SseEvent[] {
	const events: SseEvent[] = [];
	for (const block of text.split("\n\n")) {
		if (!block.trim()) continue;
		const lines = block.split("\n");
		const eventLine = lines.find((line) => line.startsWith("event: "));
		const dataLine = lines.find((line) => line.startsWith("data: "));
		if (!eventLine) continue;
		let data: unknown = null;
		if (dataLine) {
			try {
				data = JSON.parse(dataLine.slice("data: ".length));
			} catch {
				data = null;
			}
		}
		events.push({ event: eventLine.slice("event: ".length).trim(), data });
	}
	return events;
}

export async function postSse(
	path: string,
	body?: unknown,
): Promise<SseEvent[]> {
	const response = await fetch(`/api${path}`, {
		method: "POST",
		credentials: "include",
		headers:
			body !== undefined ? { "Content-Type": "application/json" } : undefined,
		body: body !== undefined ? JSON.stringify(body) : undefined,
	});
	if (!response.ok) {
		throw new Error(`Request failed with ${response.status}`);
	}
	return parseSseEvents(await response.text());
}
