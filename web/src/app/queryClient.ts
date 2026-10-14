import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";

/** Retry only transient failures: network errors and 5xx (never 4xx or not_implemented). */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (!(error instanceof ApiError)) return false;
  if (error.code === "not_implemented") return false;
  return error.status === 0 || error.status >= 500;
}

/**
 * The app's QueryClient. `networkMode: "always"`: the API is local, so requests must not pause when
 * the browser reports being offline.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        gcTime: 5 * 60_000,
        retry: shouldRetryQuery,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        refetchOnWindowFocus: true,
        networkMode: "always",
      },
      mutations: {
        retry: false,
        networkMode: "always",
      },
    },
  });
}
