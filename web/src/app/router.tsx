/**
 * Route table (UI.md §4, §16): the auth pages render in `AuthLayout`; the landing page renders on
 * its own at `/welcome` (everyone) and at `/` for signed-out visitors (the index loader decides via
 * `me`); every other page renders in the `AppShell` behind `RequireAuth`. Pages are lazy-loaded
 * from `web/src/pages/`.
 */
import type { QueryClient } from "@tanstack/react-query";
import type { ComponentType } from "react";
import { createBrowserRouter, redirect, type LoaderFunctionArgs, type RouteObject } from "react-router";
import { ApiError, apiFetch } from "../api/client";
import { qk } from "../api/hooks";
import type { Me, Project } from "../api/types";
import { readLocal, readLocalId, removeLocal, STORAGE_KEYS } from "../lib/storage";
import { AppShell } from "./AppShell";
import { AuthLayout } from "./AuthLayout";
import { paths } from "./paths";
import { RequireAuth } from "./RequireAuth";
import { AppLoading, RouteError } from "./RouteFallbacks";

type PageModule = { default: ComponentType };

/** Lazy route properties for a page module (code-split per page). */
function page(load: () => Promise<PageModule>) {
  return { Component: async () => (await load()).default };
}

/**
 * `/` when signed in: go to the last project's feed when `rr.lastProjectId` still exists on the
 * server, else `/projects`. The key is cleared when the server says the project is gone (404/422) or
 * the stored value is malformed; other failures (network, 401, 5xx) keep it (the auth gate of the
 * target route then handles them).
 */
export function createRootRedirectLoader(queryClient: QueryClient) {
  return async function rootRedirectLoader() {
    const projectId = readLocalId(STORAGE_KEYS.lastProjectId);
    if (projectId === null) {
      if (readLocal(STORAGE_KEYS.lastProjectId) !== null) removeLocal(STORAGE_KEYS.lastProjectId);
      return redirect(paths.projects());
    }
    try {
      await queryClient.fetchQuery({
        queryKey: qk.project(projectId),
        queryFn: ({ signal }) => apiFetch<Project>(`/api/projects/${projectId}`, { signal }),
        retry: false,
      });
      return redirect(paths.feed(projectId));
    } catch (error) {
      if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
        removeLocal(STORAGE_KEYS.lastProjectId);
      }
      return redirect(paths.projects());
    }
  };
}

/**
 * `/`: signed-out visitors (accounts mode, `GET /api/me` → 401) stay and see the landing page;
 * everyone else (signed in, or local mode where `me` always succeeds) gets the root redirect above.
 * A failed check (network, 5xx) also takes the redirect, so the app's auth gate shows its retry
 * screen as before. The answer is cached under `qk.me()`, so `RequireAuth` does not ask again.
 */
export function createRootLoader(queryClient: QueryClient) {
  const redirectHome = createRootRedirectLoader(queryClient);
  return async function rootLoader() {
    try {
      await queryClient.fetchQuery({
        queryKey: qk.me(),
        queryFn: ({ signal }) => apiFetch<Me>("/api/me", { signal }),
        staleTime: 60_000,
        retry: false,
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null;
    }
    return redirectHome();
  };
}

/** Element for redirect-only routes (their loaders always redirect before it would render). */
function Redirecting() {
  return null;
}

/** `/projects/:projectId` → `/projects/:projectId/feed` (query string kept). */
export function projectRedirectLoader({ params, request }: LoaderFunctionArgs) {
  const search = new URL(request.url).search;
  return redirect(`/projects/${params.projectId ?? ""}/feed${search}`);
}

export function createRoutes(queryClient: QueryClient): RouteObject[] {
  return [
    {
      id: "auth",
      Component: AuthLayout,
      ErrorBoundary: RouteError,
      HydrateFallback: AppLoading,
      children: [
        { path: "signin", lazy: page(() => import("../pages/SignInPage")) },
        { path: "signup", lazy: page(() => import("../pages/SignUpPage")) },
        { path: "forgot", lazy: page(() => import("../pages/ForgotPasswordPage")) },
        { path: "reset", lazy: page(() => import("../pages/ResetPasswordPage")) },
        { path: "verify", lazy: page(() => import("../pages/VerifyEmailPage")) },
      ],
    },
    {
      id: "root",
      path: "/",
      ErrorBoundary: RouteError,
      HydrateFallback: AppLoading,
      children: [
        // Public: the landing page (signed-out `/`, everyone at `/welcome`). No auth gate.
        { index: true, loader: createRootLoader(queryClient), lazy: page(() => import("../pages/LandingPage")) },
        { path: "welcome", lazy: page(() => import("../pages/LandingPage")) },
        {
          id: "app",
          Component: RequireAuth,
          children: [
            {
              Component: AppShell,
              children: [
                { path: "projects", lazy: page(() => import("../pages/ProjectsPage")) },
                { path: "projects/new", lazy: page(() => import("../pages/NewProjectPage")) },
                { path: "projects/:projectId", loader: projectRedirectLoader, Component: Redirecting },
                { path: "projects/:projectId/feed", lazy: page(() => import("../pages/FeedPage")) },
                { path: "projects/:projectId/library", lazy: page(() => import("../pages/LibraryPage")) },
                // One route for /graph and /graph/:paperId so GraphPage stays mounted while re-centering.
                { path: "projects/:projectId/graph/:paperId?", lazy: page(() => import("../pages/GraphPage")) },
                { path: "projects/:projectId/documents", lazy: page(() => import("../pages/DocumentsPage")) },
                { path: "projects/:projectId/settings", lazy: page(() => import("../pages/ProjectSettingsPage")) },
                { path: "papers/:paperId", lazy: page(() => import("../pages/PaperPage")) },
                { path: "dashboard", lazy: page(() => import("../pages/DashboardPage")) },
                { path: "settings", lazy: page(() => import("../pages/SettingsPage")) },
                { path: "*", lazy: page(() => import("../pages/NotFoundPage")) },
              ],
            },
          ],
        },
      ],
    },
  ];
}

export function createAppRouter(queryClient: QueryClient) {
  return createBrowserRouter(createRoutes(queryClient));
}
