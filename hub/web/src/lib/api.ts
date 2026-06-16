import { toast } from "sonner";

interface ApiError {
  detail?: unknown;
}

/**
 * Coerce a FastAPI error `detail` into a displayable string. For 422 responses
 * `detail` is an array of `{type, loc, msg, input}` objects — rendering that
 * directly as a toast/React child throws React error #31, so flatten it here.
 */
function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) =>
        d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d),
      )
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      /* fall through */
    }
  }
  return "Помилка запиту";
}

async function apiFetch<T>(
  url: string,
  options?: RequestInit,
  silent = false,
  timeoutMs = 10_000,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });

    if (!res.ok) {
      const err: ApiError = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      const msg = formatDetail(err.detail);
      if (!silent) toast.error(msg);
      throw new Error(msg);
    }

    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      if (!silent) toast.error("Запит перевищив час очікування");
      throw new Error("Request timeout");
    }
    if (!silent && !(e instanceof Error && e.message.startsWith("HTTP"))) {
      toast.error("Мережева помилка");
    }
    throw e;
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  get: <T>(url: string, silent?: boolean) =>
    apiFetch<T>(url, { method: "GET" }, silent),

  post: <T>(url: string, body?: unknown, silent?: boolean) =>
    apiFetch<T>(url, { method: "POST", body: JSON.stringify(body) }, silent),

  put: <T>(url: string, body?: unknown, silent?: boolean, timeoutMs?: number) =>
    apiFetch<T>(url, { method: "PUT", body: JSON.stringify(body) }, silent, timeoutMs),

  patch: <T>(url: string, body?: unknown, silent?: boolean) =>
    apiFetch<T>(url, { method: "PATCH", body: JSON.stringify(body) }, silent),

  delete: <T>(url: string, silent?: boolean) =>
    apiFetch<T>(url, { method: "DELETE" }, silent),
};
