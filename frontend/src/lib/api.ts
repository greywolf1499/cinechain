import { useAuthStore } from "../store/authStore";

const API_BASE = "/api";

export class ApiError extends Error {
	status: number;
	body: unknown;

	constructor(status: number, message: string, body: unknown) {
		super(message);
		this.name = "ApiError";
		this.status = status;
		this.body = body;
	}
}

async function safeJson(response: Response): Promise<unknown> {
	const text = await response.text();
	if (!text) return null;
	try {
		return JSON.parse(text);
	} catch {
		return text;
	}
}

function extractMessage(body: unknown, fallback: string): string {
	if (body && typeof body === "object" && "detail" in body) {
		const detail = (body as { detail: unknown }).detail;
		if (typeof detail === "string") return detail;
		if (detail && typeof detail === "object" && "reason" in detail) {
			const reason = (detail as { reason?: unknown }).reason;
			if (typeof reason === "string") return reason;
		}
	}
	return fallback;
}

async function request<T>(
	method: string,
	path: string,
	body?: unknown,
): Promise<T> {
	// A Blob/File (raw upload) or FormData (multipart) is sent as-is, letting the
	// browser set the Content-Type; anything else is JSON-encoded.
	const isRaw = body instanceof Blob || body instanceof FormData;
	const response = await fetch(`${API_BASE}${path}`, {
		method,
		credentials: "include",
		headers:
			body !== undefined && !isRaw
				? { "Content-Type": "application/json" }
				: undefined,
		body: isRaw ? body : body !== undefined ? JSON.stringify(body) : undefined,
	});

	if (response.status === 401) {
		useAuthStore.getState().logout();
		if (window.location.pathname !== "/login") {
			window.location.assign("/login");
		}
		throw new ApiError(401, "Not authenticated", null);
	}

	if (!response.ok) {
		const errorBody = await safeJson(response);
		throw new ApiError(
			response.status,
			extractMessage(errorBody, response.statusText),
			errorBody,
		);
	}

	if (response.status === 204) {
		return undefined as T;
	}
	return (await safeJson(response)) as T;
}

export const api = {
	get: <T>(path: string) => request<T>("GET", path),
	post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
	patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
	put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
	delete: <T>(path: string) => request<T>("DELETE", path),
};
