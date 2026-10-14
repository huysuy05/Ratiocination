/**
 * Test helpers (FROZEN):
 * - `renderWithProviders(ui, { route, path })`: QueryClient + theme/motion + MotionConfig + a memory
 *   data router; returns Testing Library queries plus `queryClient`, `router`, `user`.
 * - `installTestTransport(handler)`: route every API call to `handler` (records requests).
 * - `jsonResponse`, `errorResponse` (API error envelope), `deferred`.
 */
import { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MotionConfig } from "motion/react";
import type { ReactElement } from "react";
import { createMemoryRouter, type RouteObject } from "react-router";
import { RouterProvider } from "react-router/dom";
import { vi } from "vitest";
import { setTransport, type Transport } from "../api/client";
import type { ErrorCode, ErrorDetails, ErrorEnvelope } from "../api/types";
import { AppProviders } from "../app/providers";

/** No retries, no garbage collection during a test, always fetch. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, staleTime: 0, networkMode: "always", refetchOnWindowFocus: false },
      mutations: { retry: false, networkMode: "always" },
    },
  });
}

export interface RenderWithProvidersOptions {
  /** Initial URL (default "/"). */
  route?: string;
  /** Route pattern for `ui` (default "*"), e.g. "/projects/:projectId/feed". */
  path?: string;
  /** Full route table instead of `ui` + `path`. */
  routes?: RouteObject[];
  queryClient?: QueryClient;
  /** MotionConfig reducedMotion (default "never"). */
  reducedMotion?: "always" | "never" | "user";
}

export function renderWithProviders(ui: ReactElement | null, options: RenderWithProvidersOptions = {}) {
  const queryClient = options.queryClient ?? createTestQueryClient();
  const routes = options.routes ?? [{ path: options.path ?? "*", element: ui }];
  const router = createMemoryRouter(routes, { initialEntries: [options.route ?? "/"] });
  const user = userEvent.setup();
  const result = render(
    <AppProviders queryClient={queryClient}>
      <MotionConfig reducedMotion={options.reducedMotion ?? "never"}>
        <RouterProvider router={router} />
      </MotionConfig>
    </AppProviders>,
  );
  return { ...result, queryClient, router, user };
}

export interface RecordedRequest {
  method: string;
  /** Path + query as passed to the transport. */
  url: string;
  path: string;
  query: URLSearchParams;
  headers: Headers;
  /** Parsed JSON body, raw FormData, or null. */
  body: unknown;
}

export type TestHandler = (request: RecordedRequest) => Response | Promise<Response>;

function parseBody(body: BodyInit | null | undefined): unknown {
  if (body === null || body === undefined) return null;
  if (typeof body === "string") {
    try {
      return JSON.parse(body) as unknown;
    } catch {
      return body;
    }
  }
  return body;
}

/** Send every API call to `handler` (reset automatically after each test). */
export function installTestTransport(handler: TestHandler) {
  const requests: RecordedRequest[] = [];
  const transport = vi.fn<Transport>(async (input, init) => {
    const url = new URL(input, "http://test.local");
    const request: RecordedRequest = {
      method: (init.method ?? "GET").toUpperCase(),
      url: input,
      path: url.pathname,
      query: url.searchParams,
      headers: new Headers(init.headers),
      body: parseBody(init.body),
    };
    requests.push(request);
    return handler(request);
  });
  setTransport(transport);
  return { transport, requests };
}

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-test", ...headers },
  });
}

export function errorResponse(
  status: number,
  code: ErrorCode,
  message = "Request failed",
  details?: ErrorDetails,
  requestId = "req-test",
): Response {
  const envelope: ErrorEnvelope = { error: { code, message, request_id: requestId, ...(details ? { details } : {}) } };
  return new Response(JSON.stringify(envelope), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
  });
}

export interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
