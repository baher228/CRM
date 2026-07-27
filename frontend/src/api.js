const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");
let sessionCsrfToken = "";
let sessionPromise = null;

export class ApiError extends Error {
  constructor(message, { status = 0, code = "request_failed", fieldErrors = {}, requestId = "", currentRecord = null, currentVersion = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
    this.requestId = requestId;
    this.currentRecord = currentRecord;
    this.currentVersion = currentVersion;
  }
}

function cookie(name) {
  if (typeof document === "undefined") return "";
  return document.cookie
    .split(";")
    .map((part) => part.trim().split("="))
    .find(([key]) => key === name)?.[1] || "";
}

function requestKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export async function initializeSession() {
  if (sessionCsrfToken) return sessionCsrfToken;
  if (!sessionPromise) {
    sessionPromise = fetch(`${API_BASE_URL}/session`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new ApiError(
            response.status === 401
              ? "Open CRM Workspace from the Windows launcher to start a secure session."
              : payload.message || "The secure local session could not be started.",
            { status: response.status, code: payload.code || "authentication_required" },
          );
        }
        sessionCsrfToken = payload.csrf_token || "";
        return sessionCsrfToken;
      })
      .finally(() => {
        sessionPromise = null;
      });
  }
  return sessionPromise;
}

export async function request(path, options = {}) {
  const { method = "GET", body, signal, query, idempotencyKey } = options;
  const search = new URLSearchParams();
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) search.set(key, value);
  });
  const url = `${API_BASE_URL}/${String(path).replace(/^\//, "")}${search.size ? `?${search}` : ""}`;
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && !sessionCsrfToken) await initializeSession();
  const csrfToken = sessionCsrfToken || decodeURIComponent(cookie("crm_csrf") || cookie("csrf_token"));
  if (csrfToken && method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  if (method !== "GET") headers["Idempotency-Key"] = idempotencyKey || requestKey();

  let response;
  try {
    response = await fetch(url, {
      method,
      credentials: "same-origin",
      headers,
      signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("The local CRM service is not responding. Your data has not been changed.", {
      code: "offline",
    });
  }

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message = payload?.message || (typeof detail === "string" ? detail : null) || `Request failed (${response.status})`;
    throw new ApiError(message, {
      status: response.status,
      code: payload?.code || "request_failed",
      fieldErrors: payload?.field_errors || {},
      requestId: payload?.request_id || response.headers.get("x-request-id") || "",
      currentRecord: payload?.current_record || null,
      currentVersion: payload?.current_version ?? payload?.current_record?.version ?? null,
    });
  }
  if (response.status !== 204 && !isJson) {
    throw new ApiError("The local CRM service returned an unexpected response. Check the application address and try again.", {
      status: response.status,
      code: "unexpected_response",
    });
  }
  return payload;
}

export const api = {
  get: (path, options) => request(path, { ...options, method: "GET" }),
  post: (path, body, options) => request(path, { ...options, method: "POST", body }),
  put: (path, body, options) => request(path, { ...options, method: "PUT", body }),
  patch: (path, body, options) => request(path, { ...options, method: "PATCH", body }),
  remove: (path, options) => request(path, { ...options, method: "DELETE" }),
};

export function unwrapList(payload) {
  return unwrapPage(payload).items;
}

export function unwrapPage(payload) {
  if (Array.isArray(payload)) return { items: payload, nextCursor: null };
  return {
    items: Array.isArray(payload?.items) ? payload.items : [],
    nextCursor: payload?.next_cursor ?? null,
  };
}

export function routeForResult(result) {
  const type = String(result?.type || result?.resource_type || result?.entity_type || "").replace(/_/g, "-");
  const id = result?.id ?? result?.entity_id;
  const routes = {
    account: "accounts",
    contact: "contacts",
    lead: "leads",
    tender: "tenders",
    opportunity: "opportunities",
    deal: "opportunities",
    project: "projects",
    proposal: "proposals",
    contract: "contracts",
    invoice: "invoices",
    "credit-note": "credit-notes",
    credit_note: "credit-notes",
    task: "tasks",
    file: "files",
  };
  return id && routes[type] ? `/${routes[type]}/${id}` : "/";
}
