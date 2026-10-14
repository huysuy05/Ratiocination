/**
 * HTTP client for the Ratiocination API (UI.md §5).
 *
 * - `apiFetch<T>(path, init)`: JSON in/out. `path` is the full API path as written in docs/API.md
 *   (e.g. "/api/projects/3/feed"). Sends the session cookie (`credentials: "same-origin"`), adds
 *   `X-Radar-Client: web` on every non-GET request (CSRF defense), parses the error envelope into
 *   `ApiError`, emits a window `radar:unauthorized` event on 401 (the auth gate then sends the user
 *   to the sign-in page), and never imposes a timeout.
 * - `download(path)`: fetch → blob → object URL; filename from Content-Disposition (BibTeX export).
 * - `upload<T>(path, file, { onProgress, signal })`: multipart field `file` via XMLHttpRequest
 *   (upload progress), same headers and error envelope.
 * - `setTransport(fn)`: replaces the network transport (mock mode, tests). Uploads go through the
 *   custom transport too, which may report progress through `init.onUploadProgress`.
 */
import type { ErrorCode, ErrorDetails, ErrorEnvelope, Job } from "./types";

/** Window event emitted on every 401 response (the auth gate redirects to the sign-in page). */
export const UNAUTHORIZED_EVENT = "radar:unauthorized";
export const CLIENT_HEADER = "X-Radar-Client";
export const CLIENT_HEADER_VALUE = "web";

export interface UnauthorizedEventDetail {
  path: string;
  request_id: string;
}

/** Error codes from the API envelope, plus `network_error` when the server cannot be reached. */
export type ApiErrorCode = ErrorCode | "network_error";

export interface ApiErrorInit {
  status: number;
  code: ApiErrorCode;
  message: string;
  request_id?: string;
  details?: ErrorDetails;
  cause?: unknown;
}

/** Every failed API call rejects with an ApiError (aborts reject with the native AbortError). */
export class ApiError extends Error {
  /** HTTP status; 0 for network errors. */
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly request_id: string;
  readonly details: ErrorDetails | undefined;

  constructor(init: ApiErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.request_id = init.request_id ?? "";
    this.details = init.details;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

// ---------------------------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------------------------

export interface TransportInit extends RequestInit {
  /** Only set by `upload()`: report upload progress as a fraction 0..1 (mock transports may simulate it). */
  onUploadProgress?: (fraction: number) => void;
}

/** A fetch-compatible function. `url` is the request path plus query string (relative URL). */
export type Transport = (url: string, init: TransportInit) => Promise<Response>;

let customTransport: Transport | null = null;

/** Replace the transport (mock mode, tests). `null` restores the network (`fetch` / XHR). */
export function setTransport(fn: Transport | null): void {
  customTransport = fn;
}

/** The custom transport in use, or null when requests go to the network. */
export function getTransport(): Transport | null {
  return customTransport;
}

function send(url: string, init: TransportInit): Promise<Response> {
  const withCookies: TransportInit = { credentials: "same-origin", ...init };
  if (customTransport) return customTransport(url, withCookies);
  // Resolve fetch at call time so tests can stub it.
  return globalThis.fetch(url, withCookies);
}

// ---------------------------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------------------------

export type QueryValue = string | number | boolean | null | undefined | readonly (string | number)[];
export type QueryParams = Record<string, QueryValue>;

export interface ApiRequestInit extends Omit<RequestInit, "body"> {
  /** JSON request body; serialized with `Content-Type: application/json`. */
  json?: unknown;
  /** Raw body (FormData, Blob, string, …) passed through untouched. Prefer `json` for JSON. */
  body?: BodyInit | null;
  /** Query parameters: null/undefined/empty arrays are skipped, arrays are comma-joined. */
  query?: QueryParams;
}

/** Append query parameters to a path. Arrays become comma lists (`states=relevant,pinned`). */
export function buildPath(path: string, query?: QueryParams): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      if (value.length > 0) params.set(key, value.join(","));
    } else {
      params.set(key, String(value));
    }
  }
  const search = params.toString();
  if (!search) return path;
  return `${path}${path.includes("?") ? "&" : "?"}${search}`;
}

/** Standard request headers: `Accept` and `X-Radar-Client: web` on non-GET methods. */
export function buildHeaders(method: string, extra?: HeadersInit, jsonBody = false): Headers {
  const headers = new Headers(extra);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (method.toUpperCase() !== "GET") headers.set(CLIENT_HEADER, CLIENT_HEADER_VALUE);
  if (jsonBody && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return headers;
}

/**
 * Call the API and parse its JSON response. The method defaults to GET, or POST when a body is
 * given. 204/empty responses resolve to `undefined`; non-JSON bodies resolve to their text.
 */
export async function apiFetch<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const { json, body, query, headers: extraHeaders, method: rawMethod, ...rest } = init;
  const hasBody = json !== undefined || (body !== undefined && body !== null);
  const method = (rawMethod ?? (hasBody ? "POST" : "GET")).toUpperCase();
  const headers = buildHeaders(method, extraHeaders, json !== undefined);
  const requestBody = json !== undefined ? JSON.stringify(json) : (body ?? undefined);

  let response: Response;
  try {
    response = await send(buildPath(path, query), { ...rest, method, headers, body: requestBody });
  } catch (error) {
    throw toRequestError(error);
  }
  return readResponse<T>(response, path);
}

async function readResponse<T>(response: Response, path: string): Promise<T> {
  const requestId = response.headers.get("X-Request-ID") ?? "";
  const text = await safeText(response);
  if (!response.ok) {
    throw errorFromResponse(response.status, response.statusText, text, requestId, path);
  }
  return parseBody<T>(text, response.headers.get("Content-Type"), response.status, requestId);
}

function parseBody<T>(text: string, contentType: string | null, status: number, requestId: string): T {
  if (status === 204 || status === 205 || text === "") return undefined as T;
  const type = (contentType ?? "").toLowerCase();
  if (type.includes("json") || type === "") {
    try {
      return JSON.parse(text) as T;
    } catch {
      if (type.includes("json")) {
        throw new ApiError({
          status,
          code: "internal_error",
          message: "The server returned a malformed response.",
          request_id: requestId,
        });
      }
    }
  }
  return text as T;
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

// ---------------------------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------------------------

export function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error: unknown = value.error;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string"
  );
}

const STATUS_CODES: Record<number, ErrorCode> = {
  401: "unauthenticated",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  413: "payload_too_large",
  415: "unsupported_media_type",
  422: "validation_error",
  429: "rate_limited",
  501: "not_implemented",
  503: "source_unavailable",
};

function codeForStatus(status: number): ErrorCode {
  return STATUS_CODES[status] ?? "internal_error";
}

/**
 * Build the ApiError for a failed response from its body text (the error envelope when present).
 * Emits `radar:unauthorized` on a 401 `unauthenticated`.
 */
export function errorFromResponse(
  status: number,
  statusText: string,
  bodyText: string,
  requestId: string,
  path: string,
): ApiError {
  let parsed: unknown = undefined;
  try {
    parsed = bodyText ? JSON.parse(bodyText) : undefined;
  } catch {
    parsed = undefined;
  }
  const error = isErrorEnvelope(parsed)
    ? new ApiError({
        status,
        code: parsed.error.code,
        message: parsed.error.message,
        request_id: typeof parsed.error.request_id === "string" && parsed.error.request_id ? parsed.error.request_id : requestId,
        details: parsed.error.details,
      })
    : new ApiError({
        status,
        code: codeForStatus(status),
        message: statusText ? `Request failed (${status} ${statusText})` : `Request failed (${status})`,
        request_id: requestId,
      });
  // Only a missing/expired session sends the user to sign in (not e.g. a wrong current password).
  if (status === 401 && error.code === "unauthenticated") emitUnauthorized({ path, request_id: error.request_id });
  return error;
}

function emitUnauthorized(detail: UnauthorizedEventDetail): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<UnauthorizedEventDetail>(UNAUTHORIZED_EVENT, { detail }));
}

export function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}

function abortError(): DOMException {
  return new DOMException("The request was aborted.", "AbortError");
}

function networkError(cause?: unknown): ApiError {
  return new ApiError({
    status: 0,
    code: "network_error",
    message: "Could not reach the Ratiocination server. Is it running?",
    cause,
  });
}

/** Aborts and ApiErrors pass through; anything else thrown by the transport is a network error. */
function toRequestError(error: unknown): unknown {
  if (isAbortError(error) || error instanceof ApiError) return error;
  return networkError(error);
}

/** `details.job` of a 409 conflict on rebuild (the already active build_deck job), else null. */
export function getConflictJob(error: unknown): Job | null {
  if (!(error instanceof ApiError) || error.code !== "conflict" || !error.details) return null;
  const details: Record<string, unknown> = error.details;
  const job = details.job;
  return typeof job === "object" && job !== null && "id" in job && "status" in job ? (job as Job) : null;
}

/** `details.document_id` of a 409 conflict on duplicate upload, else null. */
export function getConflictDocumentId(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.code !== "conflict" || !error.details) return null;
  const details: Record<string, unknown> = error.details;
  return typeof details.document_id === "number" ? details.document_id : null;
}

/** Field errors of a 422 validation_error (dotted paths such as "settings.slate_size"). */
export function getFieldErrors(error: unknown): { path: string; message: string }[] {
  if (!(error instanceof ApiError) || !error.details) return [];
  const details: Record<string, unknown> = error.details;
  const fields = details.fields;
  if (!Array.isArray(fields)) return [];
  return fields.filter(
    (field): field is { path: string; message: string } =>
      typeof field === "object" &&
      field !== null &&
      typeof (field as { path?: unknown }).path === "string" &&
      typeof (field as { message?: unknown }).message === "string",
  );
}

/** `details.retry_after_seconds` of a 429 (rate_limited / quota_exceeded), else null. */
export function getRetryAfterSeconds(error: unknown): number | null {
  if (!(error instanceof ApiError) || !error.details) return null;
  const details: Record<string, unknown> = error.details;
  const value = details.retry_after_seconds;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** A user-facing message for any thrown value. */
export function errorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

// ---------------------------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------------------------

export interface DownloadOptions {
  query?: QueryParams;
  signal?: AbortSignal;
  /** Overrides the filename from Content-Disposition. */
  filename?: string;
}

export interface DownloadResult {
  filename: string;
  size: number;
  type: string;
}

/** Strip directories and control characters from a server-provided filename. */
function sanitizeFilename(raw: string): string | null {
  const base = raw.split(/[/\\]/).pop() ?? "";
  const cleaned = base.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return cleaned && cleaned !== "." && cleaned !== ".." ? cleaned : null;
}

/**
 * Filename from a Content-Disposition header: RFC 5987 `filename*=` wins over `filename=`
 * (quoted or bare). Returns null when absent.
 */
export function parseContentDispositionFilename(header: string | null | undefined): string | null {
  if (!header) return null;
  const extended = /filename\*\s*=\s*([^;]+)/i.exec(header)?.[1];
  if (extended) {
    const value = extended.trim().replace(/^"(.*)"$/, "$1");
    const encoded = /^[^']*'[^']*'(.*)$/.exec(value)?.[1] ?? value;
    try {
      const decoded = sanitizeFilename(decodeURIComponent(encoded));
      if (decoded) return decoded;
    } catch {
      // malformed percent-encoding: fall back to filename=
    }
  }
  const quoted = /filename\s*=\s*"((?:\\.|[^"\\])*)"/i.exec(header)?.[1];
  if (quoted !== undefined) return sanitizeFilename(quoted.replace(/\\(.)/g, "$1"));
  const bare = /filename\s*=\s*([^;\s]+)/i.exec(header)?.[1];
  if (bare !== undefined) return sanitizeFilename(bare);
  return null;
}

function fallbackFilename(path: string): string {
  const segment = path.split("?")[0]?.split("/").filter(Boolean).pop();
  return (segment && sanitizeFilename(segment)) || "download";
}

function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.rel = "noopener noreferrer";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoke after the click has been processed.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

/** Download a file with the API headers (auth applies) and save it under its server filename. */
export async function download(path: string, options: DownloadOptions = {}): Promise<DownloadResult> {
  const headers = buildHeaders("GET", { Accept: "*/*" });
  let response: Response;
  try {
    response = await send(buildPath(path, options.query), { method: "GET", headers, signal: options.signal ?? null });
  } catch (error) {
    throw toRequestError(error);
  }
  if (!response.ok) {
    const text = await safeText(response);
    throw errorFromResponse(response.status, response.statusText, text, response.headers.get("X-Request-ID") ?? "", path);
  }
  const blob = await response.blob();
  const filename =
    options.filename ?? parseContentDispositionFilename(response.headers.get("Content-Disposition")) ?? fallbackFilename(path);
  saveBlob(blob, filename);
  return { filename, size: blob.size, type: blob.type };
}

// ---------------------------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------------------------

export interface UploadOptions {
  /** Upload progress as a fraction 0..1 (called with 1 when the server accepted the file). */
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
  query?: QueryParams;
}

/** Upload one file as multipart field `file` (API.md Documents). Resolves the parsed JSON body. */
export function upload<T>(path: string, file: File | Blob, options: UploadOptions = {}): Promise<T> {
  const url = buildPath(path, options.query);
  const form = new FormData();
  if (file instanceof File) form.append("file", file, file.name);
  else form.append("file", file, "upload");
  const headers = buildHeaders("POST");

  if (customTransport) {
    const transport = customTransport;
    return (async () => {
      let response: Response;
      try {
        response = await transport(url, {
          method: "POST",
          headers,
          body: form,
          signal: options.signal ?? null,
          onUploadProgress: options.onProgress,
        });
      } catch (error) {
        throw toRequestError(error);
      }
      const result = await readResponse<T>(response, path);
      options.onProgress?.(1);
      return result;
    })();
  }

  return new Promise<T>((resolve, reject) => {
    const { signal, onProgress } = options;
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const xhr = new XMLHttpRequest();
    const onAbortSignal = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener("abort", onAbortSignal);

    xhr.open("POST", url);
    headers.forEach((value, key) => xhr.setRequestHeader(key, value));
    xhr.responseType = "text";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(Math.min(1, event.loaded / event.total));
    };
    xhr.onload = () => {
      cleanup();
      const requestId = xhr.getResponseHeader("X-Request-ID") ?? "";
      const text = typeof xhr.responseText === "string" ? xhr.responseText : "";
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const result = parseBody<T>(text, xhr.getResponseHeader("Content-Type"), xhr.status, requestId);
          onProgress?.(1);
          resolve(result);
        } catch (error) {
          reject(error);
        }
      } else {
        reject(errorFromResponse(xhr.status, xhr.statusText, text, requestId, path));
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(networkError());
    };
    xhr.onabort = () => {
      cleanup();
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbortSignal, { once: true });
    xhr.send(form);
  });
}
