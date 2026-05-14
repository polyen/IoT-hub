import { toast } from "sonner";

interface ApiError {
  detail: string;
}

async function apiFetch<T>(
  url: string,
  options?: RequestInit,
  silent = false,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);

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
      if (!silent) toast.error(err.detail);
      throw new Error(err.detail);
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

  put: <T>(url: string, body?: unknown, silent?: boolean) =>
    apiFetch<T>(url, { method: "PUT", body: JSON.stringify(body) }, silent),

  delete: <T>(url: string, silent?: boolean) =>
    apiFetch<T>(url, { method: "DELETE" }, silent),
};
