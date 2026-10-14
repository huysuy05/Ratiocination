/** URL builders for every route in UI.md §4 (use these instead of hand-written strings). */
import type { DashboardDays } from "../api/types";

function withQuery(path: string, query: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  const search = params.toString();
  return search ? `${path}?${search}` : path;
}

export const paths = {
  root: () => "/",
  /** The public landing page, for everyone (signed-out visitors also see it at `/`). */
  welcome: () => "/welcome",
  signIn: (next?: string | null) => withQuery("/signin", { next: next && next !== "/" ? next : null }),
  signUp: (next?: string | null) => withQuery("/signup", { next: next && next !== "/" ? next : null }),
  forgotPassword: () => "/forgot",
  resetPassword: (token?: string) => withQuery("/reset", { token }),
  verifyEmail: (token?: string) => withQuery("/verify", { token }),
  projects: () => "/projects",
  newProject: () => "/projects/new",
  project: (projectId: number) => `/projects/${projectId}`,
  feed: (projectId: number) => `/projects/${projectId}/feed`,
  library: (projectId: number) => `/projects/${projectId}/library`,
  graph: (projectId: number, paperId?: number | null) =>
    paperId ? `/projects/${projectId}/graph/${paperId}` : `/projects/${projectId}/graph`,
  documents: (projectId: number) => `/projects/${projectId}/documents`,
  projectSettings: (projectId: number) => `/projects/${projectId}/settings`,
  /** Full paper page; `?project=` adds the project context. */
  paper: (paperId: number, projectId?: number | null) => withQuery(`/papers/${paperId}`, { project: projectId ?? null }),
  dashboard: (days?: DashboardDays | null, projectId?: number | null) =>
    withQuery("/dashboard", { days: days && days !== 30 ? days : null, project: projectId ?? null }),
  settings: () => "/settings",
} as const;

/** Route patterns (React Router syntax) matching the builders above. */
export const ROUTE_PATTERNS = {
  projectScope: "/projects/:projectId/*",
  graph: "/projects/:projectId/graph/:paperId?",
  paper: "/papers/:paperId",
} as const;
