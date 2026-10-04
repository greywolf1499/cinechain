export const ANTI_CHEAT_CODE = "anti_cheat_locked";

/** An EventSource hides HTTP statuses, so after a failed connection this re-asks the same URL and
 * reports whether the server refused it as the locked Daily Puzzle pair (403 anti_cheat_locked).
 * A 200 is aborted straight away, before the server has any solving to do. */
export async function isAntiCheatLocked(url: string): Promise<boolean> {
  const controller = new AbortController();
  try {
    const response = await fetch(url, { credentials: "include", signal: controller.signal });
    if (response.status !== 403) return false;
    const body: unknown = await response.json().catch(() => null);
    return !!body && typeof body === "object" && (body as { code?: unknown }).code === ANTI_CHEAT_CODE;
  } catch {
    return false;
  } finally {
    controller.abort();
  }
}
