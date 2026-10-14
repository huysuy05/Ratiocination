import { afterEach, describe, expect, it, vi } from "vitest";
import { errorResponse, installTestTransport, jsonResponse } from "../test/utils";
import {
  ApiError,
  apiFetch,
  buildPath,
  download,
  getConflictDocumentId,
  getConflictJob,
  getFieldErrors,
  parseContentDispositionFilename,
  UNAUTHORIZED_EVENT,
  upload,
  type UnauthorizedEventDetail,
} from "./client";
import type { Job } from "./types";

const job: Job = {
  id: 7,
  kind: "build_deck",
  status: "running",
  project_id: 1,
  params: {},
  stats: {},
  progress: 0.5,
  message: "Ranking candidates",
  error_code: null,
  error_message: null,
  created_at: "2026-10-12T08:00:00Z",
  started_at: "2026-10-12T08:00:01Z",
  finished_at: null,
};

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the promise to reject");
}

describe("apiFetch headers", () => {
  it("sends no X-Radar-Client header on GET, but asks for JSON", async () => {
    const { requests } = installTestTransport(() => jsonResponse([{ id: 1 }]));
    const data = await apiFetch<{ id: number }[]>("/api/projects");
    expect(data).toEqual([{ id: 1 }]);
    expect(requests[0]?.method).toBe("GET");
    expect(requests[0]?.headers.has("X-Radar-Client")).toBe(false);
    expect(requests[0]?.headers.get("Accept")).toBe("application/json");
  });

  it.each(["POST", "PUT", "PATCH", "DELETE"])("sends X-Radar-Client: web on %s", async (method) => {
    const { requests } = installTestTransport(() => jsonResponse({ ok: true }));
    await apiFetch("/api/projects/1", { method, json: { name: "x" } });
    expect(requests[0]?.method).toBe(method);
    expect(requests[0]?.headers.get("X-Radar-Client")).toBe("web");
  });

  it("serializes `json` bodies with a JSON content type and defaults the method to POST", async () => {
    const { requests } = installTestTransport(() => jsonResponse({ ok: true }));
    await apiFetch("/api/projects", { json: { name: "Radar", research_question: "Q?" } });
    expect(requests[0]?.method).toBe("POST");
    expect(requests[0]?.headers.get("Content-Type")).toBe("application/json");
    expect(requests[0]?.body).toEqual({ name: "Radar", research_question: "Q?" });
  });

  it("sends the session cookie (same-origin credentials) and never an Authorization header", async () => {
    const { requests, transport } = installTestTransport(() => jsonResponse({}));
    await apiFetch("/api/settings");
    expect(requests[0]?.headers.has("Authorization")).toBe(false);
    expect(transport.mock.calls[0]?.[1].credentials).toBe("same-origin");
  });

  it("imposes no timeout (no signal unless the caller passes one)", async () => {
    const { transport } = installTestTransport(() => jsonResponse({}));
    await apiFetch("/api/health");
    expect(transport.mock.calls[0]?.[1].signal).toBeUndefined();
  });

  it("uses window fetch when no transport is installed", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiFetch("/api/health")).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith("/api/health", expect.objectContaining({ method: "GET" }));
  });
});

describe("apiFetch responses and errors", () => {
  it("resolves undefined for 204 No Content", async () => {
    installTestTransport(() => new Response(null, { status: 204 }));
    await expect(apiFetch("/api/projects/1", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("parses the error envelope into ApiError (status, code, message, request_id, details)", async () => {
    installTestTransport(() =>
      errorResponse(409, "conflict", "A deck is already being built", { job }, "req-abc"),
    );
    const error = await rejection(apiFetch("/api/projects/1/feed/rebuild", { method: "POST", json: { mode: "more" } }));
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(409);
    expect(apiError.code).toBe("conflict");
    expect(apiError.message).toBe("A deck is already being built");
    expect(apiError.request_id).toBe("req-abc");
    expect(apiError.details).toEqual({ job });
    expect(getConflictJob(apiError)).toEqual(job);
    expect(getConflictDocumentId(apiError)).toBeNull();
  });

  it("exposes validation field errors", async () => {
    installTestTransport(() =>
      errorResponse(422, "validation_error", "Invalid settings", {
        fields: [{ path: "settings.slate_size", message: "must be between 5 and 50" }],
      }),
    );
    const error = await rejection(apiFetch("/api/projects/1", { method: "PATCH", json: { settings: { slate_size: 1 } } }));
    expect(getFieldErrors(error)).toEqual([{ path: "settings.slate_size", message: "must be between 5 and 50" }]);
  });

  it("maps a non-envelope failure by status and keeps the X-Request-ID header", async () => {
    installTestTransport(
      () => new Response("<html>Bad gateway</html>", { status: 502, statusText: "Bad Gateway", headers: { "X-Request-ID": "rid-9" } }),
    );
    const error = (await rejection(apiFetch("/api/feed"))) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.code).toBe("internal_error");
    expect(error.request_id).toBe("rid-9");

    installTestTransport(() => new Response("", { status: 404 }));
    expect(((await rejection(apiFetch("/api/papers/999"))) as ApiError).code).toBe("not_found");
  });

  it("emits radar:unauthorized on 401 unauthenticated and rejects with that code", async () => {
    installTestTransport(() => errorResponse(401, "unauthenticated", "Sign in to continue", undefined, "rid-401"));
    const events: UnauthorizedEventDetail[] = [];
    const listener = (event: Event) => events.push((event as CustomEvent<UnauthorizedEventDetail>).detail);
    window.addEventListener(UNAUTHORIZED_EVENT, listener);
    try {
      const error = (await rejection(apiFetch("/api/projects"))) as ApiError;
      expect(error.code).toBe("unauthenticated");
      expect(error.status).toBe(401);
    } finally {
      window.removeEventListener(UNAUTHORIZED_EVENT, listener);
    }
    expect(events).toEqual([{ path: "/api/projects", request_id: "rid-401" }]);
  });

  it("does not emit radar:unauthorized for other errors (wrong password, forbidden)", async () => {
    const listener = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, listener);
    installTestTransport(() => errorResponse(401, "invalid_credentials", "Wrong email or password"));
    await rejection(apiFetch("/api/auth/signin", { method: "POST", json: {} }));
    installTestTransport(() => errorResponse(403, "unauthorized", "Origin not allowed"));
    await rejection(apiFetch("/api/projects", { method: "POST", json: {} }));
    window.removeEventListener(UNAUTHORIZED_EVENT, listener);
    expect(listener).not.toHaveBeenCalled();
  });

  it("maps statuses without an envelope to API codes", async () => {
    installTestTransport(() => new Response("nope", { status: 429, statusText: "Too Many Requests" }));
    const error = (await rejection(apiFetch("/api/auth/signin", { method: "POST", json: {} }))) as ApiError;
    expect(error.code).toBe("rate_limited");
  });

  it("wraps transport failures as network_error with status 0", async () => {
    installTestTransport(() => {
      throw new TypeError("Failed to fetch");
    });
    const error = (await rejection(apiFetch("/api/projects"))) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.code).toBe("network_error");
  });

  it("lets aborts through untouched", async () => {
    installTestTransport(() => {
      throw new DOMException("aborted", "AbortError");
    });
    const error = await rejection(apiFetch("/api/projects"));
    expect(error).not.toBeInstanceOf(ApiError);
    expect((error as DOMException).name).toBe("AbortError");
  });
});

describe("buildPath", () => {
  it("joins arrays with commas and skips null, undefined and empty arrays", () => {
    expect(
      buildPath("/api/papers", { project_id: 3, states: ["relevant", "pinned"], query: null, cursor: undefined, relations: [] }),
    ).toBe("/api/papers?project_id=3&states=relevant%2Cpinned");
    expect(buildPath("/api/papers?x=1", { limit: 5 })).toBe("/api/papers?x=1&limit=5");
    expect(buildPath("/api/jobs")).toBe("/api/jobs");
  });
});

describe("parseContentDispositionFilename", () => {
  it.each([
    ['attachment; filename="ratiocination-1.bib"', "ratiocination-1.bib"],
    ["attachment; filename=ratiocination-2.bib", "ratiocination-2.bib"],
    ["attachment; filename*=UTF-8''r%C3%A9sum%C3%A9%20draft.bib", "résumé draft.bib"],
    ["attachment; filename=\"fallback.bib\"; filename*=UTF-8''preferred.bib", "preferred.bib"],
    ['attachment; filename="../../etc/passwd"', "passwd"],
    ['attachment; filename="say \\"hi\\".bib"', 'say "hi".bib'],
  ])("%s → %s", (header, expected) => {
    expect(parseContentDispositionFilename(header)).toBe(expected);
  });

  it("returns null without a filename", () => {
    expect(parseContentDispositionFilename(null)).toBeNull();
    expect(parseContentDispositionFilename("inline")).toBeNull();
  });
});

describe("download", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fetches with the session cookie, saves the blob under the Content-Disposition filename", async () => {
    const { requests, transport } = installTestTransport(
      () =>
        new Response("@article{a}", {
          status: 200,
          headers: {
            "Content-Type": "text/x-bibtex",
            "Content-Disposition": 'attachment; filename="ratiocination-4.bib"',
          },
        }),
    );
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:radar");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const clicked: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });
    vi.useFakeTimers();

    const result = await download("/api/exports/bibtex", { query: { project_id: 4, states: ["saved", "relevant"] } });

    expect(result.filename).toBe("ratiocination-4.bib");
    expect(requests[0]?.url).toBe("/api/exports/bibtex?project_id=4&states=saved%2Crelevant");
    expect(transport.mock.calls[0]?.[1].credentials).toBe("same-origin");
    expect(requests[0]?.headers.has("X-Radar-Client")).toBe(false);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(clicked[0]?.download).toBe("ratiocination-4.bib");
    expect(clicked[0]?.getAttribute("href")).toBe("blob:radar");
    vi.runAllTimers();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:radar");
  });

  it("falls back to the last path segment and rejects with ApiError on failure", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    installTestTransport(() => new Response("x", { status: 200 }));
    await expect(download("/api/exports/bibtex?project_id=1")).resolves.toMatchObject({ filename: "bibtex" });

    installTestTransport(() => errorResponse(404, "not_found", "Project not found"));
    const error = (await rejection(download("/api/exports/bibtex?project_id=99"))) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("not_found");
  });
});

describe("upload", () => {
  it("posts multipart field `file` through the transport with the API headers", async () => {
    const accepted = { document: { id: 3 }, job };
    const { requests } = installTestTransport(() => jsonResponse(accepted, 202));
    const onProgress = vi.fn();
    const file = new File(["hello"], "draft.md", { type: "text/markdown" });

    await expect(upload("/api/projects/1/documents", file, { onProgress })).resolves.toEqual(accepted);

    const request = requests[0];
    expect(request?.method).toBe("POST");
    expect(request?.headers.get("X-Radar-Client")).toBe("web");
    expect(request?.headers.has("Authorization")).toBe(false);
    expect(request?.headers.has("Content-Type")).toBe(false);
    expect(request?.body).toBeInstanceOf(FormData);
    expect(((request?.body as FormData).get("file") as File).name).toBe("draft.md");
    expect(onProgress).toHaveBeenLastCalledWith(1);
  });

  it("parses the error envelope (duplicate upload conflict)", async () => {
    installTestTransport(() =>
      errorResponse(409, "conflict", "This file is already in the project", { document_id: 12 }),
    );
    const error = await rejection(upload("/api/projects/1/documents", new File(["x"], "a.txt")));
    expect(error).toBeInstanceOf(ApiError);
    expect(getConflictDocumentId(error)).toBe(12);
  });

  it("uses XMLHttpRequest with progress events when no transport is installed", async () => {
    class FakeXHR {
      static last: FakeXHR | null = null;
      method = "";
      url = "";
      headers: Record<string, string> = {};
      body: unknown = null;
      status = 0;
      statusText = "";
      responseText = "";
      responseType = "";
      upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onabort: (() => void) | null = null;
      constructor() {
        FakeXHR.last = this;
      }
      open(method: string, url: string) {
        this.method = method;
        this.url = url;
      }
      setRequestHeader(name: string, value: string) {
        this.headers[name.toLowerCase()] = value;
      }
      getResponseHeader(name: string) {
        return name.toLowerCase() === "content-type" ? "application/json" : null;
      }
      send(body: unknown) {
        this.body = body;
      }
      abort() {
        this.onabort?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const onProgress = vi.fn();
    const pending = upload<{ ok: boolean }>("/api/projects/2/documents", new File(["abc"], "paper.pdf"), { onProgress });

    const xhr = FakeXHR.last;
    expect(xhr?.method).toBe("POST");
    expect(xhr?.url).toBe("/api/projects/2/documents");
    expect(xhr?.headers["x-radar-client"]).toBe("web");
    expect(xhr?.body).toBeInstanceOf(FormData);

    xhr?.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent);
    expect(onProgress).toHaveBeenCalledWith(0.5);

    if (xhr) {
      xhr.status = 202;
      xhr.responseText = JSON.stringify({ ok: true });
      xhr.onload?.();
    }
    await expect(pending).resolves.toEqual({ ok: true });
    expect(onProgress).toHaveBeenLastCalledWith(1);
  });
});
