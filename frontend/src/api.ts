import { currentLang } from "./i18n";
import type { EntityPatch, Job, ServerConfig } from "./types";

const KEY = "redactor.client-id";

/** Identifies this browser to the server. No account, no password - just a scope key. */
export function clientId(): string {
  let id = localStorage.getItem(KEY);
  if (!id) {
    id = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/-/g, "");
    localStorage.setItem(KEY, id);
  }
  return id;
}

/** A failed request, carrying whatever the server managed to tell us. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly requestId: string | null,
    readonly network: boolean,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function unwrap<T>(response: Response): Promise<T> {
  const requestId = response.headers.get("X-Request-Id");
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(detail, response.status, requestId, false);
  }
  return response.json() as Promise<T>;
}

/** Is the backend reachable at all? Used to explain a bare network failure. */
async function serverAlive(): Promise<boolean> {
  try {
    const probe = await fetch("/health", { cache: "no-store" });
    return probe.ok;
  } catch {
    return false;
  }
}

/**
 * fetch() rejects with a bare TypeError ("Failed to fetch") for anything that stops the
 * request completing - server down or restarting, connection dropped mid-upload, request
 * abandoned after a stall, CORS refusal. None of those carry a status code, so this
 * probes /health to tell "the server is gone" from "that one call failed", logs the
 * detail for the console, and raises a message a person can act on.
 */
async function send<T>(label: string, run: () => Promise<Response>): Promise<T> {
  const started = performance.now();
  try {
    const response = await run();
    const result = await unwrap<T>(response);
    console.debug(
      `[api] ${label} ${response.status} in ${Math.round(performance.now() - started)}ms` +
        ` req=${response.headers.get("X-Request-Id") ?? "-"}`,
    );
    return result;
  } catch (error) {
    if (error instanceof ApiError) {
      console.warn(`[api] ${label} failed: ${error.message} req=${error.requestId ?? "-"}`);
      throw error;
    }
    const elapsed = Math.round(performance.now() - started);
    const alive = await serverAlive();
    console.error(
      `[api] ${label} network failure after ${elapsed}ms; server reachable: ${alive}`,
      error,
    );
    throw new ApiError(
      alive ? networkMessage("dropped", elapsed) : networkMessage("down", elapsed),
      null,
      null,
      true,
    );
  }
}

/** Set by the app so network errors can be phrased in the chosen language. */
let networkMessage: (kind: "down" | "dropped", ms: number) => string = (kind) =>
  kind === "down"
    ? "Cannot reach the server."
    : "The connection dropped before the server answered.";

export function setNetworkMessenger(fn: typeof networkMessage): void {
  networkMessage = fn;
}

function headers(json = false): HeadersInit {
  const base: Record<string, string> = {
    "X-Client-Id": clientId(),
    // Backend warnings and errors come back already translated.
    "Accept-Language": currentLang(),
  };
  if (json) base["Content-Type"] = "application/json";
  return base;
}

export const api = {
  config: () => send<ServerConfig>("config", () => fetch("/api/config", { headers: headers() })),

  scan(files: File[]): Promise<Job> {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    return send<Job>("scan", () =>
      fetch("/api/anonymize/jobs", { method: "POST", headers: headers(), body: form }),
    );
  },

  job: (id: string) =>
    send<Job>("job", () => fetch(`/api/anonymize/jobs/${id}`, { headers: headers() })),

  patchEntities: (id: string, entities: EntityPatch[]) =>
    send<Job>("patchEntities", () =>
      fetch(`/api/anonymize/jobs/${id}/entities`, {
        method: "PATCH",
        headers: headers(true),
        body: JSON.stringify({ entities }),
      }),
    ),

  redact: (id: string) =>
    send<Job>("redact", () =>
      fetch(`/api/anonymize/jobs/${id}/redact`, { method: "POST", headers: headers() }),
    ),

  restore(files: File[], mapping: File): Promise<Job> {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    form.append("mapping", mapping);
    return send<Job>("restore", () =>
      fetch("/api/deanonymize/jobs", { method: "POST", headers: headers(), body: form }),
    );
  },

  discard: (id: string) =>
    fetch(`/api/jobs/${id}`, { method: "DELETE", headers: headers() }),
};

/** Downloads go through fetch so the client id header travels with them. */
export async function download(path: string, fallbackName: string): Promise<void> {
  const response = await fetch(path, { headers: headers() });
  if (!response.ok) throw new Error("That file is not available.");
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const name = match ? decodeURIComponent(match[1]) : fallbackName;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export const paths = {
  bundle: (id: string) => `/api/anonymize/jobs/${id}/bundle`,
  mapping: (id: string) => `/api/anonymize/jobs/${id}/mapping.json`,
  redactedDoc: (id: string, doc: string) => `/api/anonymize/jobs/${id}/documents/${doc}`,
  restoredBundle: (id: string) => `/api/deanonymize/jobs/${id}/bundle`,
  restoredDoc: (id: string, doc: string) => `/api/deanonymize/jobs/${id}/documents/${doc}`,
};
