/**
 * Server-state hooks (UI.md §5).
 *
 * - `qk`: the query-key factory. Prefixes never overlap across resources, so invalidating
 *   `qk.project(id)` does not touch `qk.feed(id)`.
 * - One query hook per GET in docs/API.md and one mutation hook per non-GET.
 * - `useFeedback` / `useUndo`: per-project serialized requests, optimistic states, an in-memory
 *   undo stack with exit directions, rollback + toast on error.
 * - `useJob`: polls a job every 1.5 s until it succeeds or fails.
 * - Signing in or out clears the whole cache: another user's data must never show.
 *
 * Feature-specific hooks belong in `web/src/features/<feature>/` and use `apiFetch` + `qk`.
 */
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryKey,
  type UseInfiniteQueryResult,
  type UseMutationResult,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { ACTION_TO_STATE, isStateAction, type ExitDirection, type StateAction } from "../lib/states";
import { readLocalId, removeLocal, STORAGE_KEYS } from "../lib/storage";
import { ApiError, apiFetch, download, errorMessage, isAbortError, upload, type DownloadResult } from "./client";
import type {
  AccountDeleteRequest,
  AuthConfig,
  Breakthroughs,
  DashboardActivity,
  DashboardDays,
  DashboardFindings,
  DashboardTopics,
  DashboardWork,
  DisruptionDetail,
  DocumentAccepted,
  DocumentDeleted,
  DocumentInfo,
  ExpandResponse,
  FeedResponse,
  FeedbackRequest,
  FeedbackResponse,
  ForgotPasswordRequest,
  Health,
  Job,
  JobAccepted,
  JobKind,
  LibraryItem,
  LibraryPage,
  Me,
  MeUpdate,
  Neighborhood,
  OAuthProvider,
  PaperDetail,
  PaperState,
  PaperSummary,
  PasswordChangeRequest,
  Project,
  ProjectCreate,
  ProjectGraph,
  ProjectUpdate,
  ReadMark,
  RebuildRequest,
  ResendVerificationRequest,
  ResetPasswordRequest,
  RevokedSessions,
  RunJobRequest,
  ScholarDetail,
  SearchResults,
  Seed,
  SeedCreate,
  SeedCreated,
  SettingsUpdate,
  SignInRequest,
  SignUpRequest,
  SummaryRequest,
  SystemSettings,
  UndoResponse,
  VerificationPending,
  VerifyEmailRequest,
} from "./types";

// =============================================================================================
// Constants and small helpers
// =============================================================================================

/** Clients poll every 1.5 s (API.md conventions). */
export const POLL_INTERVAL_MS = 1500;
export const FEED_POLL_MS = POLL_INTERVAL_MS;
export const JOB_POLL_MS = POLL_INTERVAL_MS;
export const DOCUMENT_POLL_MS = POLL_INTERVAL_MS;

export const DEFAULT_LIBRARY_LIMIT = 50;
export const DEFAULT_SEARCH_LIMIT = 20;
export const DEFAULT_PROJECT_GRAPH_LIMIT = 150;
export const DEFAULT_JOBS_LIMIT = 20;

/** True for a positive integer id (route params parsed with Number() may be NaN). */
export function isValidId(id: number | null | undefined): id is number {
  return typeof id === "number" && Number.isSafeInteger(id) && id > 0;
}

/** Parse a route/search param into a positive integer id, else null. */
export function parseId(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined || !/^[1-9]\d{0,15}$/.test(raw.trim())) return null;
  const id = Number(raw.trim());
  return Number.isSafeInteger(id) ? id : null;
}

export function isJobTerminal(job: Pick<Job, "status"> | null | undefined): boolean {
  return job?.status === "succeeded" || job?.status === "failed";
}

export function isJobActive(job: Pick<Job, "status"> | null | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

function isDocumentPending(doc: Pick<DocumentInfo, "status">): boolean {
  return doc.status === "uploaded" || doc.status === "processing";
}

function normalizeQuery(query: string | null | undefined): string | null {
  const trimmed = query?.trim();
  return trimmed ? trimmed : null;
}

function normalizeList<T extends string>(values: readonly T[] | null | undefined): T[] | null {
  if (!values || values.length === 0) return null;
  return [...new Set(values)].sort();
}

/** Sentinel id for disabled queries (never fetched). */
const NO_ID = -1;

/**
 * Projects deleted by this client. Their queries are disabled at once: a page that is still mounted
 * while the next route loads would otherwise refetch them into 404s.
 */
const deletedProjects = new Set<number>();

/** A valid project id that this client has not deleted. */
function isLiveProject(projectId: number | null | undefined): projectId is number {
  return isValidId(projectId) && !deletedProjects.has(projectId);
}

/** The browser's IANA time zone ("UTC" when unknown). */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

// =============================================================================================
// Query keys
// =============================================================================================

export type LibraryStateFilter = Extract<PaperState, "relevant" | "saved">;
export type ReadFilter = "any" | "read" | "unread";

export interface LibraryFilters {
  /** Omit for both relevant and saved. */
  states?: readonly LibraryStateFilter[] | null;
  read?: ReadFilter | null;
  query?: string | null;
  /** Page size, default 50. */
  limit?: number | null;
}

export interface NormalizedLibraryFilters {
  states: LibraryStateFilter[] | null;
  read: ReadFilter;
  query: string | null;
  limit: number;
}

export function normalizeLibraryFilters(filters: LibraryFilters = {}): NormalizedLibraryFilters {
  return {
    states: normalizeList(filters.states),
    read: filters.read ?? "any",
    query: normalizeQuery(filters.query),
    limit: filters.limit ?? DEFAULT_LIBRARY_LIMIT,
  };
}

export interface JobFilters {
  limit?: number | null;
  kind?: JobKind | null;
  project_id?: number | null;
}

function normalizeJobFilters(filters: JobFilters = {}): JobFilters | null {
  const normalized: JobFilters = {};
  if (filters.limit !== undefined && filters.limit !== null && filters.limit !== DEFAULT_JOBS_LIMIT) {
    normalized.limit = filters.limit;
  }
  if (filters.kind) normalized.kind = filters.kind;
  if (filters.project_id !== undefined && filters.project_id !== null) normalized.project_id = filters.project_id;
  return Object.keys(normalized).length > 0 ? normalized : null;
}

/** Scope of every dashboard view (API.md "Dashboard"). */
export interface DashboardParams {
  days: DashboardDays;
  projectId: number | null;
  tz: string;
}

function dashboardQuery(params: DashboardParams) {
  return { days: params.days, project_id: isValidId(params.projectId) ? params.projectId : null, tz: params.tz };
}

export type DashboardSection = "activity" | "topics" | "findings" | "work";

export const qk = {
  authConfig: () => ["authConfig"] as const,
  me: () => ["me"] as const,
  projects: () => ["projects"] as const,
  project: (projectId: number) => ["project", projectId] as const,
  feed: (projectId: number) => ["feed", projectId] as const,
  /** Paper detail; `projectId` null = without project context. Prefix of all details: `qk.papers()`. */
  paper: (paperId: number, projectId: number | null) => ["paper", paperId, projectId ?? null] as const,
  paperAll: (paperId: number) => ["paper", paperId] as const,
  papers: () => ["paper"] as const,
  /** Library list (infinite). Prefix for one project: `qk.libraryAll(projectId)`. */
  library: (projectId: number, filters: LibraryFilters = {}) =>
    ["library", projectId, normalizeLibraryFilters(filters)] as const,
  libraryAll: (projectId: number) => ["library", projectId] as const,
  libraries: () => ["library"] as const,
  /** Local title/abstract search over all papers (GET /api/search). */
  search: (query: string, limit: number = DEFAULT_SEARCH_LIMIT) => ["search", query.trim(), limit] as const,
  seeds: (projectId: number) => ["seeds", projectId] as const,
  neighborhood: (paperId: number, projectId: number | null) => ["neighborhood", paperId, projectId ?? null] as const,
  neighborhoods: () => ["neighborhood"] as const,
  projectGraph: (projectId: number, limit?: number) =>
    limit === undefined || limit === DEFAULT_PROJECT_GRAPH_LIMIT
      ? (["projectGraph", projectId] as const)
      : (["projectGraph", projectId, { limit }] as const),
  projectGraphs: () => ["projectGraph"] as const,
  breakthroughs: (paperId: number) => ["breakthroughs", paperId] as const,
  scholar: (scholarId: number) => ["scholar", scholarId] as const,
  documents: (projectId: number) => ["documents", projectId] as const,
  document: (documentId: number) => ["document", documentId] as const,
  /** Dashboard views; `qk.dashboard()` is the prefix of every section. */
  dashboard: (section?: DashboardSection, params?: DashboardParams) =>
    section === undefined
      ? (["dashboard"] as const)
      : params === undefined
        ? (["dashboard", section] as const)
        : (["dashboard", section, dashboardQuery(params)] as const),
  /** Jobs list; `qk.jobs()` (no filters) is also the prefix of every jobs list. */
  jobs: (filters: JobFilters = {}) => {
    const normalized = normalizeJobFilters(filters);
    return normalized ? (["jobs", normalized] as const) : (["jobs"] as const);
  },
  job: (jobId: number) => ["job", jobId] as const,
  settings: () => ["settings"] as const,
  health: () => ["health"] as const,
};

// =============================================================================================
// Query hooks (one per GET in docs/API.md)
// =============================================================================================

/** Extra options for query hooks (queryKey/queryFn are fixed by the hook). */
export type QueryOptions<T> = Omit<UseQueryOptions<T, ApiError, T>, "queryKey" | "queryFn">;

/** GET /api/auth/config (public). */
export function useAuthConfig(options?: QueryOptions<AuthConfig>): UseQueryResult<AuthConfig, ApiError> {
  return useQuery({
    staleTime: 5 * 60_000,
    ...options,
    queryKey: qk.authConfig(),
    queryFn: ({ signal }) => apiFetch<AuthConfig>("/api/auth/config", { signal }),
  });
}

/** GET /api/me — the signed-in user (401 `unauthenticated` when signed out). */
export function useMe(options?: QueryOptions<Me>): UseQueryResult<Me, ApiError> {
  return useQuery({
    staleTime: 60_000,
    retry: (failureCount, error) => failureCount < 2 && error.status !== 401 && (error.status === 0 || error.status >= 500),
    ...options,
    queryKey: qk.me(),
    queryFn: ({ signal }) => apiFetch<Me>("/api/me", { signal }),
  });
}

/** GET /api/projects (newest first). */
export function useProjects(options?: QueryOptions<Project[]>): UseQueryResult<Project[], ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.projects(),
    queryFn: ({ signal }) => apiFetch<Project[]>("/api/projects", { signal }),
  });
}

/** GET /api/projects/{id}. */
export function useProject(
  projectId: number | null | undefined,
  options?: QueryOptions<Project>,
): UseQueryResult<Project, ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.project(projectId ?? NO_ID),
    queryFn: ({ signal }) => apiFetch<Project>(`/api/projects/${projectId}`, { signal }),
    enabled: isLiveProject(projectId) && (options?.enabled ?? true),
  });
}

/** GET /api/projects/{id}/seeds. */
export function useSeeds(projectId: number | null | undefined, options?: QueryOptions<Seed[]>): UseQueryResult<Seed[], ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.seeds(projectId ?? NO_ID),
    queryFn: ({ signal }) => apiFetch<Seed[]>(`/api/projects/${projectId}/seeds`, { signal }),
    enabled: isLiveProject(projectId) && (options?.enabled ?? true),
  });
}

/**
 * GET /api/projects/{id}/feed. Polls every 1.5 s while `status === "building"`. Window-focus
 * refetches are off so a refetch never races the deck's optimistic states.
 */
export function useFeed(
  projectId: number | null | undefined,
  options?: QueryOptions<FeedResponse>,
): UseQueryResult<FeedResponse, ApiError> {
  return useQuery({
    refetchOnWindowFocus: false,
    refetchInterval: (query) => (query.state.data?.status === "building" ? FEED_POLL_MS : false),
    ...options,
    queryKey: qk.feed(projectId ?? NO_ID),
    queryFn: ({ signal }) => apiFetch<FeedResponse>(`/api/projects/${projectId}/feed`, { signal }),
    enabled: isLiveProject(projectId) && (options?.enabled ?? true),
  });
}

/** GET /api/papers/{id}?project_id= (state/recommendation are null without a project). */
export function usePaper(
  paperId: number | null | undefined,
  projectId: number | null | undefined,
  options?: QueryOptions<PaperDetail>,
): UseQueryResult<PaperDetail, ApiError> {
  const pid = isValidId(projectId) ? projectId : null;
  return useQuery({
    ...options,
    queryKey: qk.paper(paperId ?? NO_ID, pid),
    queryFn: ({ signal }) => apiFetch<PaperDetail>(`/api/papers/${paperId}`, { signal, query: { project_id: pid } }),
    enabled: isValidId(paperId) && (options?.enabled ?? true),
  });
}

/** Concatenate library pages (dedupe by paper id); total from the first page, cursor from the last. */
export function mergeLibraryPages(pages: readonly LibraryPage[]): LibraryPage {
  const seen = new Set<number>();
  const items: LibraryItem[] = [];
  for (const page of pages) {
    for (const item of page.items) {
      if (seen.has(item.paper.id)) continue;
      seen.add(item.paper.id);
      items.push(item);
    }
  }
  return {
    items,
    total: pages[0]?.total ?? 0,
    next_cursor: pages.length > 0 ? (pages[pages.length - 1]?.next_cursor ?? null) : null,
  };
}

const selectLibrary = (data: InfiniteData<LibraryPage, string | null>): LibraryPage => mergeLibraryPages(data.pages);

/**
 * GET /api/papers?project_id=&states=&read=&query=&limit=&cursor= — the project library as an
 * infinite query; `data` is the merged LibraryPage (use `fetchNextPage`/`hasNextPage` for more).
 */
export function useLibrary(
  projectId: number | null | undefined,
  filters: LibraryFilters = {},
  options?: { enabled?: boolean },
): UseInfiniteQueryResult<LibraryPage, ApiError> {
  const normalized = normalizeLibraryFilters(filters);
  return useInfiniteQuery({
    queryKey: qk.library(projectId ?? NO_ID, normalized),
    queryFn: ({ pageParam, signal }) =>
      apiFetch<LibraryPage>("/api/papers", {
        signal,
        query: {
          project_id: projectId,
          states: normalized.states,
          read: normalized.read === "any" ? null : normalized.read,
          query: normalized.query,
          limit: normalized.limit,
          cursor: pageParam,
        },
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    select: selectLibrary,
    enabled: isLiveProject(projectId) && (options?.enabled ?? true),
  });
}

/** GET /api/search?query=&limit= — local search over all papers. Disabled while blank. */
export function useSearch(
  query: string,
  options?: QueryOptions<SearchResults> & { limit?: number },
): UseQueryResult<SearchResults, ApiError> {
  const { limit = DEFAULT_SEARCH_LIMIT, ...rest } = options ?? {};
  const trimmed = query.trim();
  return useQuery({
    ...rest,
    queryKey: qk.search(trimmed, limit),
    queryFn: ({ signal }) => apiFetch<SearchResults>("/api/search", { signal, query: { query: trimmed, limit } }),
    enabled: trimmed.length > 0 && (rest.enabled ?? true),
  });
}

function expansionPending(data: { expansion: { status: string } } | undefined): boolean {
  return data?.expansion.status === "queued" || data?.expansion.status === "running";
}

/** GET /api/papers/{id}/neighborhood?project_id=. Polls every 1.5 s while an expansion runs. */
export function useNeighborhood(
  paperId: number | null | undefined,
  projectId: number | null | undefined,
  options?: QueryOptions<Neighborhood>,
): UseQueryResult<Neighborhood, ApiError> {
  const pid = isValidId(projectId) ? projectId : null;
  return useQuery({
    refetchInterval: (query) => (expansionPending(query.state.data) ? JOB_POLL_MS : false),
    ...options,
    queryKey: qk.neighborhood(paperId ?? NO_ID, pid),
    queryFn: ({ signal }) =>
      apiFetch<Neighborhood>(`/api/papers/${paperId}/neighborhood`, { signal, query: { project_id: pid } }),
    enabled: isValidId(paperId) && (options?.enabled ?? true),
  });
}

/** GET /api/projects/{id}/graph?limit=150. */
export function useProjectGraph(
  projectId: number | null | undefined,
  options?: QueryOptions<ProjectGraph> & { limit?: number },
): UseQueryResult<ProjectGraph, ApiError> {
  const { limit = DEFAULT_PROJECT_GRAPH_LIMIT, ...rest } = options ?? {};
  return useQuery({
    ...rest,
    queryKey: qk.projectGraph(projectId ?? NO_ID, limit),
    queryFn: ({ signal }) => apiFetch<ProjectGraph>(`/api/projects/${projectId}/graph`, { signal, query: { limit } }),
    enabled: isLiveProject(projectId) && (rest.enabled ?? true),
  });
}

/** GET /api/papers/{id}/breakthroughs. Polls every 1.5 s while an analysis is queued/running. */
export function useBreakthroughs(
  paperId: number | null | undefined,
  options?: QueryOptions<Breakthroughs>,
): UseQueryResult<Breakthroughs, ApiError> {
  return useQuery({
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? JOB_POLL_MS : false;
    },
    ...options,
    queryKey: qk.breakthroughs(paperId ?? NO_ID),
    queryFn: ({ signal }) => apiFetch<Breakthroughs>(`/api/papers/${paperId}/breakthroughs`, { signal }),
    enabled: isValidId(paperId) && (options?.enabled ?? true),
  });
}

/** GET /api/scholars/{id}. */
export function useScholar(
  scholarId: number | null | undefined,
  options?: QueryOptions<ScholarDetail>,
): UseQueryResult<ScholarDetail, ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.scholar(scholarId ?? NO_ID),
    queryFn: ({ signal }) => apiFetch<ScholarDetail>(`/api/scholars/${scholarId}`, { signal }),
    enabled: isValidId(scholarId) && (options?.enabled ?? true),
  });
}

/** GET /api/projects/{id}/documents. Polls every 1.5 s while any document is uploaded/processing. */
export function useDocuments(
  projectId: number | null | undefined,
  options?: QueryOptions<DocumentInfo[]>,
): UseQueryResult<DocumentInfo[], ApiError> {
  return useQuery({
    refetchInterval: (query) => (query.state.data?.some(isDocumentPending) ? DOCUMENT_POLL_MS : false),
    ...options,
    queryKey: qk.documents(projectId ?? NO_ID),
    queryFn: ({ signal }) => apiFetch<DocumentInfo[]>(`/api/projects/${projectId}/documents`, { signal }),
    enabled: isLiveProject(projectId) && (options?.enabled ?? true),
  });
}

/** GET /api/documents/{id}/status. Polls every 1.5 s while uploaded/processing. */
export function useDocumentStatus(
  documentId: number | null | undefined,
  options?: QueryOptions<DocumentInfo>,
): UseQueryResult<DocumentInfo, ApiError> {
  return useQuery({
    refetchInterval: (query) => {
      const doc = query.state.data;
      return doc && !isDocumentPending(doc) ? false : DOCUMENT_POLL_MS;
    },
    ...options,
    queryKey: qk.document(documentId ?? NO_ID),
    queryFn: ({ signal }) => apiFetch<DocumentInfo>(`/api/documents/${documentId}/status`, { signal }),
    enabled: isValidId(documentId) && (options?.enabled ?? true),
  });
}

/** GET /api/dashboard/activity. */
export function useDashboardActivity(
  params: DashboardParams,
  options?: QueryOptions<DashboardActivity>,
): UseQueryResult<DashboardActivity, ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.dashboard("activity", params),
    queryFn: ({ signal }) => apiFetch<DashboardActivity>("/api/dashboard/activity", { signal, query: dashboardQuery(params) }),
  });
}

/** GET /api/dashboard/topics. */
export function useDashboardTopics(
  params: DashboardParams,
  options?: QueryOptions<DashboardTopics>,
): UseQueryResult<DashboardTopics, ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.dashboard("topics", params),
    queryFn: ({ signal }) => apiFetch<DashboardTopics>("/api/dashboard/topics", { signal, query: dashboardQuery(params) }),
  });
}

/** GET /api/dashboard/findings. Polls every 1.5 s while an AI summary job is queued/running. */
export function useDashboardFindings(
  params: DashboardParams,
  options?: QueryOptions<DashboardFindings>,
): UseQueryResult<DashboardFindings, ApiError> {
  return useQuery({
    refetchInterval: (query) => (isJobActive(query.state.data?.ai.job) ? JOB_POLL_MS : false),
    ...options,
    queryKey: qk.dashboard("findings", params),
    queryFn: ({ signal }) => apiFetch<DashboardFindings>("/api/dashboard/findings", { signal, query: dashboardQuery(params) }),
  });
}

/** GET /api/dashboard/work. */
export function useDashboardWork(
  params: DashboardParams,
  options?: QueryOptions<DashboardWork>,
): UseQueryResult<DashboardWork, ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.dashboard("work", params),
    queryFn: ({ signal }) => apiFetch<DashboardWork>("/api/dashboard/work", { signal, query: dashboardQuery(params) }),
  });
}

/** GET /api/jobs?limit=20&kind=&project_id= (newest first). Polls while any listed job is active. */
export function useJobs(filters: JobFilters = {}, options?: QueryOptions<Job[]>): UseQueryResult<Job[], ApiError> {
  return useQuery({
    refetchInterval: (query) => (query.state.data?.some(isJobActive) ? JOB_POLL_MS : false),
    ...options,
    queryKey: qk.jobs(filters),
    queryFn: ({ signal }) =>
      apiFetch<Job[]>("/api/jobs", {
        signal,
        query: { limit: filters.limit ?? DEFAULT_JOBS_LIMIT, kind: filters.kind, project_id: filters.project_id },
      }),
  });
}

export interface UseJobOptions {
  /** Poll interval while queued/running (default 1500 ms). */
  intervalMs?: number;
  /** Called once when the job reaches succeeded/failed (after related queries are invalidated). */
  onSettled?: (job: Job) => void;
  enabled?: boolean;
}

/**
 * GET /api/jobs/{id}, polled every `intervalMs` until the job succeeds or fails. On settle it
 * invalidates the queries the job kind affects (see `invalidateForJob`) and calls `onSettled` once.
 */
export function useJob(jobId: number | null | undefined, options: UseJobOptions = {}): UseQueryResult<Job, ApiError> {
  const { intervalMs = JOB_POLL_MS, onSettled, enabled = true } = options;
  const queryClient = useQueryClient();
  const query = useQuery<Job, ApiError>({
    queryKey: qk.job(jobId ?? NO_ID),
    queryFn: ({ signal }) => apiFetch<Job>(`/api/jobs/${jobId}`, { signal }),
    enabled: isValidId(jobId) && enabled,
    staleTime: 0,
    refetchInterval: (q) => (isJobTerminal(q.state.data) ? false : intervalMs),
  });

  const onSettledRef = useRef(onSettled);
  useEffect(() => {
    onSettledRef.current = onSettled;
  });
  const settledJobRef = useRef<number | null>(null);
  const job = query.data;

  useEffect(() => {
    if (!job || job.id !== jobId || !isJobTerminal(job)) return;
    if (settledJobRef.current === job.id) return;
    settledJobRef.current = job.id;
    void invalidateForJob(queryClient, job);
    onSettledRef.current?.(job);
  }, [job, jobId, queryClient]);

  return query;
}

/** GET /api/settings. */
export function useSettings(options?: QueryOptions<SystemSettings>): UseQueryResult<SystemSettings, ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.settings(),
    queryFn: ({ signal }) => apiFetch<SystemSettings>("/api/settings", { signal }),
  });
}

/** GET /api/health. */
export function useHealth(options?: QueryOptions<Health>): UseQueryResult<Health, ApiError> {
  return useQuery({
    ...options,
    queryKey: qk.health(),
    queryFn: ({ signal }) => apiFetch<Health>("/api/health", { signal }),
  });
}

// =============================================================================================
// Invalidation helpers
// =============================================================================================

function invalidateAll(queryClient: QueryClient, keys: readonly QueryKey[]): Promise<void> {
  return Promise.all(keys.map((queryKey) => queryClient.invalidateQueries({ queryKey }))).then(() => undefined);
}

/**
 * What every feedback/undo settles — `qk.project`, `['library', projectId]`, `qk.projectGraph`,
 * `['neighborhood']`, the dashboard (never `qk.feed`: the deck keeps its order) — plus the acted-on
 * paper's project detail, so a detail load that raced the action heals.
 */
export function invalidateAfterFeedback(queryClient: QueryClient, projectId: number, paperId?: number): Promise<void> {
  const tasks = [
    invalidateAll(queryClient, [
      qk.project(projectId),
      qk.libraryAll(projectId),
      qk.projectGraph(projectId),
      qk.neighborhoods(),
      qk.dashboard(),
    ]),
  ];
  if (paperId !== undefined) tasks.push(refreshPaperDetail(queryClient, qk.paper(paperId, projectId)));
  return Promise.all(tasks).then(() => undefined);
}

/**
 * Refetch a paper detail after an action. An initial load that was already in flight predates the
 * action, and TanStack would fold the invalidation into it, so wait for that load first.
 */
async function refreshPaperDetail(queryClient: QueryClient, queryKey: QueryKey): Promise<void> {
  const state = queryClient.getQueryState(queryKey);
  if (!state) return;
  if (state.data === undefined && state.fetchStatus === "fetching") {
    await queryClient.refetchQueries({ queryKey, exact: true }).catch(() => undefined);
  }
  await queryClient.invalidateQueries({ queryKey, exact: true });
}

/** Queries affected by a finished job, by kind. */
export function invalidateForJob(queryClient: QueryClient, job: Job): Promise<void> {
  const pid = job.project_id;
  const keys: QueryKey[] = [qk.jobs()];
  switch (job.kind) {
    case "daily":
    case "build_deck":
      keys.push(qk.projects(), qk.dashboard());
      if (pid !== null) keys.push(qk.feed(pid), qk.project(pid), qk.projectGraph(pid));
      else keys.push(["feed"], ["project"], qk.projectGraphs());
      break;
    case "enrich":
      keys.push(qk.papers(), qk.neighborhoods(), qk.dashboard());
      if (pid !== null) keys.push(qk.seeds(pid), qk.project(pid), qk.feed(pid));
      else keys.push(["seeds"], ["project"]);
      break;
    case "expand":
      keys.push(qk.papers(), qk.neighborhoods(), qk.projectGraphs());
      break;
    case "breakthroughs":
      keys.push(["breakthroughs"], ["scholar"], qk.papers(), qk.neighborhoods());
      break;
    case "process_document":
      keys.push(["document"], qk.dashboard("work"));
      if (pid !== null) keys.push(qk.documents(pid), qk.project(pid), qk.feed(pid));
      else keys.push(["documents"], ["project"]);
      break;
    case "summarize_findings":
      keys.push(qk.dashboard("findings"));
      break;
    case "download_model":
      keys.push(qk.settings(), qk.health());
      break;
    case "ingest_feeds":
    case "backfill":
    case "embed":
      break;
  }
  return invalidateAll(queryClient, keys);
}

function requireProjectId(projectId: number | null | undefined): number {
  if (!isValidId(projectId)) {
    throw new ApiError({ status: 0, code: "validation_error", message: "No project is selected." });
  }
  return projectId;
}

// =============================================================================================
// Feedback, undo stack and optimistic states
// =============================================================================================

/** One undoable action on the per-project in-memory stack (UI.md §5). */
export interface UndoEntry {
  interaction_id: number;
  paper_id: number;
  action: StateAction;
  /** Where the card left the deck (Undo flies it back in from this side). */
  exit_direction: ExitDirection | null;
  slate_id: number | null;
  /** The paper's state before the action, as cached at the time (informational). */
  previous_state: PaperState | null;
}

export interface UndoStackSnapshot {
  /** Something can be undone: an unclaimed pending request or stack entry exists. */
  canUndo: boolean;
  /** Top of the stack (completed actions only). */
  top: UndoEntry | null;
  /** Number of completed entries on the stack. */
  depth: number;
  /** Feedback requests (state actions) queued or in flight. */
  pending: number;
}

interface StackItem {
  token: number;
  entry: UndoEntry;
}

interface FeedbackStore {
  enqueue<T>(task: () => Promise<T>): Promise<T>;
  register(): number;
  succeed(token: number, entry: UndoEntry): void;
  fail(token: number): void;
  claimTarget(): number | null;
  release(token: number): void;
  take(token: number): StackItem | null;
  takeByInteraction(interactionId: number): StackItem | null;
  restore(item: StackItem): void;
  clear(): void;
  subscribe(listener: () => void): () => void;
  getSnapshot(): UndoStackSnapshot;
}

const EMPTY_UNDO_SNAPSHOT: UndoStackSnapshot = { canUndo: false, top: null, depth: 0, pending: 0 };

function createFeedbackStore(): FeedbackStore {
  let tail: Promise<void> = Promise.resolve();
  let nextToken = 1;
  let pending: number[] = [];
  let stack: StackItem[] = [];
  const claimed = new Set<number>();
  const listeners = new Set<() => void>();
  let snapshot: UndoStackSnapshot = EMPTY_UNDO_SNAPSHOT;

  const emit = () => {
    const tokens = [...pending, ...stack.map((item) => item.token)];
    snapshot = {
      canUndo: tokens.some((token) => !claimed.has(token)),
      top: stack[stack.length - 1]?.entry ?? null,
      depth: stack.length,
      pending: pending.length,
    };
    for (const listener of listeners) listener();
  };
  const insertSorted = (item: StackItem) => {
    stack = [...stack, item].sort((a, b) => a.token - b.token);
  };
  const removeAt = (index: number): StackItem | null => {
    const item = stack[index];
    if (!item) return null;
    stack = stack.filter((_, i) => i !== index);
    emit();
    return item;
  };

  return {
    enqueue<T>(task: () => Promise<T>): Promise<T> {
      const run = tail.then(task);
      tail = run.then(
        () => undefined,
        () => undefined,
      );
      return run;
    },
    register() {
      const token = nextToken++;
      pending = [...pending, token];
      emit();
      return token;
    },
    succeed(token, entry) {
      pending = pending.filter((t) => t !== token);
      insertSorted({ token, entry });
      emit();
    },
    fail(token) {
      pending = pending.filter((t) => t !== token);
      emit();
    },
    claimTarget() {
      const candidates = [...pending, ...stack.map((item) => item.token)].filter((t) => !claimed.has(t));
      if (candidates.length === 0) return null;
      const target = Math.max(...candidates);
      claimed.add(target);
      emit();
      return target;
    },
    release(token) {
      claimed.delete(token);
      emit();
    },
    take(token) {
      return removeAt(stack.findIndex((item) => item.token === token));
    },
    takeByInteraction(interactionId) {
      return removeAt(stack.findIndex((item) => item.entry.interaction_id === interactionId));
    },
    restore(item) {
      insertSorted(item);
      emit();
    },
    clear() {
      stack = [];
      claimed.clear();
      emit();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot() {
      return snapshot;
    },
  };
}

const feedbackStores = new Map<number, FeedbackStore>();

function getFeedbackStore(projectId: number): FeedbackStore {
  let store = feedbackStores.get(projectId);
  if (!store) {
    store = createFeedbackStore();
    feedbackStores.set(projectId, store);
  }
  return store;
}

/** Empty a project's undo stack (e.g. when the deck's slate id changes). Pending requests continue. */
export function clearUndoStack(projectId: number): void {
  feedbackStores.get(projectId)?.clear();
}

/** Current undo-stack snapshot of a project (non-reactive; components use `useUndo`). */
export function getUndoStackSnapshot(projectId: number): UndoStackSnapshot {
  return feedbackStores.get(projectId)?.getSnapshot() ?? EMPTY_UNDO_SNAPSHOT;
}

/** Forget every project's queue, undo stack and deleted mark (tests; signing in or out). */
export function resetFeedbackState(): void {
  feedbackStores.clear();
  deletedProjects.clear();
}

interface PaperStatePatch {
  state: PaperState | null;
  /** undefined = keep the cached value. */
  last_interaction_id?: number | null;
}

interface CachedPaperState {
  state: PaperState | null;
  last_interaction_id: number | null;
}

interface OptimisticSnapshot {
  feed: CachedPaperState | null;
  paper: CachedPaperState | null;
}

/** Patch one paper's state in the project's feed items (keeps `slate.remaining` consistent). */
function patchFeedItem(queryClient: QueryClient, projectId: number, paperId: number, patch: PaperStatePatch): void {
  queryClient.setQueryData<FeedResponse>(qk.feed(projectId), (feed) => {
    if (!feed?.slate) return feed;
    let changed = false;
    const items = feed.slate.items.map((item) => {
      if (item.paper.id !== paperId) return item;
      changed = true;
      return {
        ...item,
        state: patch.state,
        last_interaction_id: patch.last_interaction_id === undefined ? item.last_interaction_id : patch.last_interaction_id,
      };
    });
    if (!changed) return feed;
    return { ...feed, slate: { ...feed.slate, items, remaining: items.filter((item) => item.state === null).length } };
  });
}

/** Patch one paper's state in its project-scoped detail (and its embedded recommendation). */
function patchPaperDetail(queryClient: QueryClient, projectId: number, paperId: number, patch: PaperStatePatch): void {
  queryClient.setQueryData<PaperDetail>(qk.paper(paperId, projectId), (paper) => {
    if (!paper) return paper;
    const lastInteractionId = patch.last_interaction_id === undefined ? paper.last_interaction_id : patch.last_interaction_id;
    return {
      ...paper,
      state: patch.state,
      last_interaction_id: lastInteractionId,
      recommendation: paper.recommendation
        ? {
            ...paper.recommendation,
            state: patch.state,
            last_interaction_id:
              patch.last_interaction_id === undefined ? paper.recommendation.last_interaction_id : patch.last_interaction_id,
          }
        : null,
    };
  });
}

/** Apply a paper state to every cached view that holds it (feed items + paper detail). */
export function setCachedPaperState(
  queryClient: QueryClient,
  projectId: number,
  paperId: number,
  state: PaperState | null,
  lastInteractionId?: number | null,
): void {
  const patch: PaperStatePatch = { state, last_interaction_id: lastInteractionId };
  patchFeedItem(queryClient, projectId, paperId, patch);
  patchPaperDetail(queryClient, projectId, paperId, patch);
}

function readCachedStates(queryClient: QueryClient, projectId: number, paperId: number): OptimisticSnapshot {
  const item = queryClient.getQueryData<FeedResponse>(qk.feed(projectId))?.slate?.items.find((i) => i.paper.id === paperId);
  const paper = queryClient.getQueryData<PaperDetail>(qk.paper(paperId, projectId));
  return {
    feed: item ? { state: item.state, last_interaction_id: item.last_interaction_id } : null,
    paper: paper ? { state: paper.state, last_interaction_id: paper.last_interaction_id } : null,
  };
}

function cancelRefetch(queryClient: QueryClient, queryKey: QueryKey): Promise<void> {
  if (queryClient.getQueryData(queryKey) === undefined) return Promise.resolve();
  return queryClient.cancelQueries({ queryKey, exact: true });
}

async function applyOptimisticState(
  queryClient: QueryClient,
  projectId: number,
  paperId: number,
  state: PaperState,
): Promise<OptimisticSnapshot> {
  // Cancel in-flight refetches first so their (older) results cannot overwrite the optimistic
  // state. Initial loads are left alone: cancelling one would leave its query pending forever.
  await Promise.all([cancelRefetch(queryClient, qk.feed(projectId)), cancelRefetch(queryClient, qk.paper(paperId, projectId))]);
  const snapshot = readCachedStates(queryClient, projectId, paperId);
  patchFeedItem(queryClient, projectId, paperId, { state });
  patchPaperDetail(queryClient, projectId, paperId, { state });
  return snapshot;
}

function rollbackOptimisticState(
  queryClient: QueryClient,
  projectId: number,
  paperId: number,
  snapshot: OptimisticSnapshot,
): void {
  if (snapshot.feed) patchFeedItem(queryClient, projectId, paperId, snapshot.feed);
  if (snapshot.paper) patchPaperDetail(queryClient, projectId, paperId, snapshot.paper);
}

/** `mutate()` variables of useFeedback: the API request plus the card's exit direction. */
export interface FeedbackVariables extends FeedbackRequest {
  exitDirection?: ExitDirection | null;
}

async function runFeedback(
  queryClient: QueryClient,
  projectId: number | null | undefined,
  variables: FeedbackVariables,
): Promise<FeedbackResponse> {
  const pid = requireProjectId(projectId);
  const store = getFeedbackStore(pid);
  const { exitDirection, ...request } = variables;
  const body: FeedbackRequest = {
    paper_id: request.paper_id,
    action: request.action,
    slate_id: request.slate_id ?? null,
    ...(request.context ? { context: request.context } : {}),
  };
  const post = () => apiFetch<FeedbackResponse>(`/api/projects/${pid}/feedback`, { method: "POST", json: body });

  const action = request.action;
  if (!isStateAction(action)) {
    // "open" changes nothing: serialized, but no optimistic state and no undo entry.
    return store.enqueue(post);
  }

  // Registered synchronously so an undo requested right after this call targets this action.
  const token = store.register();
  const optimistic = applyOptimisticState(queryClient, pid, request.paper_id, ACTION_TO_STATE[action]).catch(
    () => null,
  );
  // All bookkeeping happens inside the queued task, so the next queued request (e.g. an undo)
  // only starts once the undo stack and caches reflect this one.
  return store.enqueue(async () => {
    const before = await optimistic;
    try {
      const response = await post();
      setCachedPaperState(queryClient, pid, response.paper_id, response.state, response.interaction_id);
      store.succeed(token, {
        interaction_id: response.interaction_id,
        paper_id: response.paper_id,
        action,
        exit_direction: exitDirection ?? null,
        slate_id: body.slate_id ?? null,
        previous_state: before?.feed ? before.feed.state : (before?.paper?.state ?? null),
      });
      return response;
    } catch (error) {
      store.fail(token);
      if (before) rollbackOptimisticState(queryClient, pid, request.paper_id, before);
      throw error;
    }
  });
}

/**
 * POST /api/projects/{id}/feedback. `mutate({ paper_id, action, slate_id, context, exitDirection })`.
 * Requests are serialized per project; the state is applied optimistically to `qk.feed` items and
 * `qk.paper`, then replaced by the server's answer; successful state actions are pushed on the
 * project's undo stack. On error: rollback + error toast. "open" is serialized too, but has no
 * optimistic state, undo entry, toast or invalidation.
 */
export function useFeedback(
  projectId: number | null | undefined,
): UseMutationResult<FeedbackResponse, ApiError, FeedbackVariables> {
  const queryClient = useQueryClient();
  return useMutation<FeedbackResponse, ApiError, FeedbackVariables>({
    mutationKey: ["feedback", projectId ?? null],
    networkMode: "always",
    retry: false,
    mutationFn: (variables) => runFeedback(queryClient, projectId, variables),
    onError: (error, variables) => {
      if (variables.action === "open" || isAbortError(error)) return;
      toast.error(errorMessage(error, "Could not save your feedback."));
    },
    onSettled: (_data, _error, variables) => {
      if (variables.action === "open" || !isValidId(projectId)) return;
      void invalidateAfterFeedback(queryClient, projectId, variables.paper_id);
    },
  });
}

/** `mutate()` variables of useUndo: omit to pop the stack, or pass a specific interaction id. */
export interface UndoVariables {
  interaction_id?: number | null;
}

/** The server's answer plus the stack entry that was undone (null for an explicit id not on the stack). */
export interface UndoResult extends UndoResponse {
  entry: UndoEntry | null;
}

async function runUndo(
  queryClient: QueryClient,
  projectId: number | null | undefined,
  variables: UndoVariables,
): Promise<UndoResult | null> {
  const pid = requireProjectId(projectId);
  const store = getFeedbackStore(pid);
  const explicitId = variables.interaction_id ?? null;
  // Claim the target now (the most recent action not yet claimed), so a request that is still
  // pending is the one undone once it lands; if it fails instead, this undo does nothing.
  const target = explicitId === null ? store.claimTarget() : null;
  if (explicitId === null && target === null) return null;

  return store.enqueue(async () => {
    let item: StackItem | null;
    if (target !== null) {
      store.release(target);
      item = store.take(target);
      if (!item) return null;
    } else {
      item = explicitId !== null ? store.takeByInteraction(explicitId) : null;
    }
    const interactionId = explicitId ?? item?.entry.interaction_id ?? null;
    if (interactionId === null) return null;
    try {
      const response = await apiFetch<UndoResponse>(`/api/projects/${pid}/feedback/undo`, {
        method: "POST",
        json: { interaction_id: interactionId },
      });
      setCachedPaperState(queryClient, pid, response.paper_id, response.state, response.last_interaction_id);
      return { ...response, entry: item?.entry ?? null };
    } catch (error) {
      // 404 = nothing to undo server-side; anything else keeps the entry for another try.
      if (item && !(error instanceof ApiError && error.status === 404)) store.restore(item);
      throw error;
    }
  });
}

export type UseUndoResult = UseMutationResult<UndoResult | null, ApiError, UndoVariables | void> & {
  /** True when there is an action to undo (including feedback still in flight). */
  canUndo: boolean;
  /** The most recent completed action on the stack. */
  top: UndoEntry | null;
  /** Empty this project's undo stack. */
  clear: () => void;
};

const noopSubscribe = () => () => undefined;
const emptySnapshot = () => EMPTY_UNDO_SNAPSHOT;

/**
 * POST /api/projects/{id}/feedback/undo. `mutate()` pops the project's undo stack (waiting for any
 * pending feedback POST first); `mutate({ interaction_id })` undoes a specific interaction (e.g.
 * the detail sheet's `last_interaction_id`). Resolves `null` when there was nothing to undo.
 */
export function useUndo(projectId: number | null | undefined): UseUndoResult {
  const queryClient = useQueryClient();
  const store = isValidId(projectId) ? getFeedbackStore(projectId) : null;
  const snapshot = useSyncExternalStore(
    store ? store.subscribe : noopSubscribe,
    store ? store.getSnapshot : emptySnapshot,
    store ? store.getSnapshot : emptySnapshot,
  );
  const mutation = useMutation<UndoResult | null, ApiError, UndoVariables | void>({
    mutationKey: ["undo", projectId ?? null],
    networkMode: "always",
    retry: false,
    mutationFn: (variables) => runUndo(queryClient, projectId, variables ?? {}),
    onError: (error) => {
      if (isAbortError(error)) return;
      toast.error(errorMessage(error, "Could not undo."));
    },
    onSettled: (result) => {
      if (result === null || !isValidId(projectId)) return;
      void invalidateAfterFeedback(queryClient, projectId, result?.paper_id);
    },
  });
  const clear = useCallback(() => {
    if (isValidId(projectId)) clearUndoStack(projectId);
  }, [projectId]);
  return { ...mutation, canUndo: snapshot.canUndo, top: snapshot.top, clear };
}

// =============================================================================================
// Read marks
// =============================================================================================

/** Apply a read mark to every cached copy of the paper (feeds, details, libraries, searches). */
export function setCachedReadAt(queryClient: QueryClient, paperId: number, readAt: string | null): void {
  const patchPaper = <T extends PaperSummary>(paper: T): T => (paper.id === paperId ? { ...paper, read_at: readAt } : paper);
  queryClient.setQueriesData<FeedResponse>({ queryKey: ["feed"] }, (feed) =>
    feed?.slate
      ? { ...feed, slate: { ...feed.slate, items: feed.slate.items.map((item) => ({ ...item, paper: patchPaper(item.paper) })) } }
      : feed,
  );
  queryClient.setQueriesData<PaperDetail>({ queryKey: qk.paperAll(paperId) }, (paper) =>
    paper ? { ...paper, read_at: readAt } : paper,
  );
  queryClient.setQueriesData<InfiniteData<LibraryPage, string | null>>({ queryKey: qk.libraries() }, (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((page) => ({
            ...page,
            items: page.items.map((item) => ({ ...item, paper: patchPaper(item.paper) })),
          })),
        }
      : data,
  );
}

export interface SetReadVariables {
  paperId: number;
  read: boolean;
}

/** PUT /api/papers/{id}/read `{ read }` — optimistic everywhere the paper is cached. */
export function useSetRead(): UseMutationResult<ReadMark, ApiError, SetReadVariables, { previous: string | null }> {
  const queryClient = useQueryClient();
  return useMutation<ReadMark, ApiError, SetReadVariables, { previous: string | null }>({
    mutationFn: ({ paperId, read }) => apiFetch<ReadMark>(`/api/papers/${paperId}/read`, { method: "PUT", json: { read } }),
    onMutate: ({ paperId, read }) => {
      const cached = queryClient.getQueriesData<PaperDetail>({ queryKey: qk.paperAll(paperId) })[0]?.[1];
      const previous = cached?.read_at ?? null;
      setCachedReadAt(queryClient, paperId, read ? new Date().toISOString() : null);
      return { previous };
    },
    onError: (error, { paperId }, context) => {
      setCachedReadAt(queryClient, paperId, context?.previous ?? null);
      toast.error(errorMessage(error, "Could not update the read mark."));
    },
    onSuccess: (mark) => setCachedReadAt(queryClient, mark.paper_id, mark.read_at),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.dashboard("activity") });
    },
  });
}

// =============================================================================================
// Accounts
// =============================================================================================

/** After signing in, out or deleting the account: drop every cached query and undo stack. */
function resetSession(queryClient: QueryClient, me: Me | null): void {
  queryClient.cancelQueries();
  queryClient.clear();
  resetFeedbackState();
  if (me) queryClient.setQueryData(qk.me(), me);
}

/** POST /api/auth/signin → Me (+ session cookie). */
export function useSignIn(): UseMutationResult<Me, ApiError, SignInRequest> {
  const queryClient = useQueryClient();
  return useMutation<Me, ApiError, SignInRequest>({
    mutationFn: (body) => apiFetch<Me>("/api/auth/signin", { method: "POST", json: body }),
    onSuccess: (me) => resetSession(queryClient, me),
  });
}

/** A sign-up answer that is waiting for email verification (202, no session). */
export function isVerificationPending(value: Me | VerificationPending): value is VerificationPending {
  return "verification_required" in value && value.verification_required === true;
}

/** POST /api/auth/signup → 201 Me (+ session cookie), or 202 VerificationPending (no session yet). */
export function useSignUp(): UseMutationResult<Me | VerificationPending, ApiError, SignUpRequest> {
  const queryClient = useQueryClient();
  return useMutation<Me | VerificationPending, ApiError, SignUpRequest>({
    mutationFn: (body) => apiFetch<Me | VerificationPending>("/api/auth/signup", { method: "POST", json: body }),
    onSuccess: (result) => {
      if (!isVerificationPending(result)) resetSession(queryClient, result);
    },
  });
}

/** POST /api/auth/signout → 204; clears every cached query. */
export function useSignOut(): UseMutationResult<void, ApiError, void> {
  const queryClient = useQueryClient();
  return useMutation<void, ApiError, void>({
    mutationFn: () => apiFetch<void>("/api/auth/signout", { method: "POST" }),
    onSettled: () => resetSession(queryClient, null),
  });
}

/** Full-page navigation to an OAuth provider (`next` = where to land afterwards). */
export function oauthStartUrl(provider: OAuthProvider, next: string, link = false): string {
  const params = new URLSearchParams({ next });
  if (link) params.set("link", "1");
  return `/api/auth/oauth/${provider}/start?${params.toString()}`;
}

/** POST /api/auth/password/forgot → 202 (always). */
export function useForgotPassword(): UseMutationResult<void, ApiError, ForgotPasswordRequest> {
  return useMutation<void, ApiError, ForgotPasswordRequest>({
    mutationFn: (body) => apiFetch<void>("/api/auth/password/forgot", { method: "POST", json: body }),
  });
}

/** POST /api/auth/password/reset → Me (+ session cookie; other sessions revoked). */
export function useResetPassword(): UseMutationResult<Me, ApiError, ResetPasswordRequest> {
  const queryClient = useQueryClient();
  return useMutation<Me, ApiError, ResetPasswordRequest>({
    mutationFn: (body) => apiFetch<Me>("/api/auth/password/reset", { method: "POST", json: body }),
    onSuccess: (me) => resetSession(queryClient, me),
  });
}

/** POST /api/auth/email/verify → Me (+ session cookie); 401 `password_required` outside the browser that asked for the link. */
export function useVerifyEmail(): UseMutationResult<Me, ApiError, VerifyEmailRequest> {
  const queryClient = useQueryClient();
  return useMutation<Me, ApiError, VerifyEmailRequest>({
    mutationFn: (body) => apiFetch<Me>("/api/auth/email/verify", { method: "POST", json: body }),
    onSuccess: (me) => resetSession(queryClient, me),
  });
}

/** POST /api/auth/email/resend (signed in, no body) → 202 (409 when verified or without SMTP). */
export function useResendVerification(): UseMutationResult<void, ApiError, void> {
  return useMutation<void, ApiError, void>({
    mutationFn: () => apiFetch<void>("/api/auth/email/resend", { method: "POST" }),
  });
}

/** POST /api/auth/email/resend `{ email }` (signed out) → 202 whether or not the address has an account. */
export function useResendVerificationEmail(): UseMutationResult<void, ApiError, ResendVerificationRequest> {
  return useMutation<void, ApiError, ResendVerificationRequest>({
    mutationFn: (body) => apiFetch<void>("/api/auth/email/resend", { method: "POST", json: body }),
  });
}

/** PATCH /api/me. */
export function useUpdateMe(): UseMutationResult<Me, ApiError, MeUpdate> {
  const queryClient = useQueryClient();
  return useMutation<Me, ApiError, MeUpdate>({
    mutationFn: (body) => apiFetch<Me>("/api/me", { method: "PATCH", json: body }),
    onSuccess: (me, body) => {
      queryClient.setQueryData(qk.me(), me);
      if (body.preferences?.timezone !== undefined) void queryClient.invalidateQueries({ queryKey: qk.dashboard() });
    },
  });
}

/** POST /api/me/password (other sessions are revoked). */
export function useChangePassword(): UseMutationResult<Me, ApiError, PasswordChangeRequest> {
  const queryClient = useQueryClient();
  return useMutation<Me, ApiError, PasswordChangeRequest>({
    mutationFn: (body) => apiFetch<Me>("/api/me/password", { method: "POST", json: body }),
    onSuccess: (me) => queryClient.setQueryData(qk.me(), me),
  });
}

/** DELETE /api/me/identities/{provider} (409 when it is the last sign-in method). */
export function useUnlinkIdentity(): UseMutationResult<Me, ApiError, OAuthProvider> {
  const queryClient = useQueryClient();
  return useMutation<Me, ApiError, OAuthProvider>({
    mutationFn: (provider) => apiFetch<Me>(`/api/me/identities/${provider}`, { method: "DELETE" }),
    onSuccess: (me) => queryClient.setQueryData(qk.me(), me),
  });
}

/** POST /api/me/sessions/revoke-others. */
export function useRevokeOtherSessions(): UseMutationResult<RevokedSessions, ApiError, void> {
  return useMutation<RevokedSessions, ApiError, void>({
    mutationFn: () => apiFetch<RevokedSessions>("/api/me/sessions/revoke-others", { method: "POST" }),
  });
}

/** DELETE /api/me `{ confirm_email }` → 204; clears every cached query. */
export function useDeleteAccount(): UseMutationResult<void, ApiError, AccountDeleteRequest> {
  const queryClient = useQueryClient();
  return useMutation<void, ApiError, AccountDeleteRequest>({
    mutationFn: (body) => apiFetch<void>("/api/me", { method: "DELETE", json: body }),
    onSuccess: () => resetSession(queryClient, null),
  });
}

// =============================================================================================
// Mutation hooks (one per non-GET in docs/API.md)
// =============================================================================================

/** POST /api/projects. */
export function useCreateProject(): UseMutationResult<Project, ApiError, ProjectCreate> {
  const queryClient = useQueryClient();
  return useMutation<Project, ApiError, ProjectCreate>({
    mutationFn: (body) => apiFetch<Project>("/api/projects", { method: "POST", json: body }),
    onSuccess: (project) => {
      queryClient.setQueryData(qk.project(project.id), project);
      void queryClient.invalidateQueries({ queryKey: qk.projects() });
    },
  });
}

/** PATCH /api/projects/{id} (send only the keys that changed). */
export function useUpdateProject(projectId: number | null | undefined): UseMutationResult<Project, ApiError, ProjectUpdate> {
  const queryClient = useQueryClient();
  return useMutation<Project, ApiError, ProjectUpdate>({
    mutationFn: (body) => apiFetch<Project>(`/api/projects/${requireProjectId(projectId)}`, { method: "PATCH", json: body }),
    onSuccess: (project) => {
      queryClient.setQueryData(qk.project(project.id), project);
      void invalidateAll(queryClient, [qk.projects(), qk.feed(project.id), qk.dashboard()]);
    },
  });
}

/** DELETE /api/projects/{id}. `mutate(projectId)`. Disables and drops the project's cached data. */
export function useDeleteProject(): UseMutationResult<void, ApiError, number> {
  const queryClient = useQueryClient();
  return useMutation<void, ApiError, number>({
    mutationFn: (projectId) => apiFetch<void>(`/api/projects/${requireProjectId(projectId)}`, { method: "DELETE" }),
    onMutate: (projectId) => {
      deletedProjects.add(projectId);
    },
    onError: (_error, projectId) => {
      deletedProjects.delete(projectId);
    },
    onSuccess: (_data, projectId) => {
      for (const queryKey of [
        qk.project(projectId),
        qk.feed(projectId),
        qk.libraryAll(projectId),
        qk.seeds(projectId),
        qk.documents(projectId),
        qk.projectGraph(projectId),
      ]) {
        queryClient.removeQueries({ queryKey });
      }
      clearUndoStack(projectId);
      if (readLocalId(STORAGE_KEYS.lastProjectId) === projectId) removeLocal(STORAGE_KEYS.lastProjectId);
      void invalidateAll(queryClient, [qk.projects(), qk.papers(), qk.neighborhoods(), qk.dashboard()]);
    },
  });
}

/** POST /api/projects/{id}/seeds (identifier or paper_id). */
export function useAddSeed(projectId: number | null | undefined): UseMutationResult<SeedCreated, ApiError, SeedCreate> {
  const queryClient = useQueryClient();
  return useMutation<SeedCreated, ApiError, SeedCreate>({
    mutationFn: (body) =>
      apiFetch<SeedCreated>(`/api/projects/${requireProjectId(projectId)}/seeds`, { method: "POST", json: body }),
    onSuccess: (result) => {
      if (result.enrich_job) queryClient.setQueryData(qk.job(result.enrich_job.id), result.enrich_job);
      if (!isValidId(projectId)) return;
      void invalidateAll(queryClient, [qk.seeds(projectId), qk.project(projectId), qk.projectGraph(projectId)]);
    },
  });
}

/** DELETE /api/projects/{id}/seeds/{paper_id}. `mutate(paperId)`. */
export function useRemoveSeed(projectId: number | null | undefined): UseMutationResult<void, ApiError, number> {
  const queryClient = useQueryClient();
  return useMutation<void, ApiError, number>({
    mutationFn: (paperId) =>
      apiFetch<void>(`/api/projects/${requireProjectId(projectId)}/seeds/${paperId}`, { method: "DELETE" }),
    onSuccess: () => {
      if (!isValidId(projectId)) return;
      void invalidateAll(queryClient, [qk.seeds(projectId), qk.project(projectId), qk.projectGraph(projectId)]);
    },
  });
}

/**
 * POST /api/projects/{id}/feed/rebuild `{ mode, prepare }` → 202 `{ job }`. A 409 conflict (different
 * mode already building) rejects with an ApiError whose `details.job` is the active job.
 */
export function useRebuildFeed(projectId: number | null | undefined): UseMutationResult<JobAccepted, ApiError, RebuildRequest> {
  const queryClient = useQueryClient();
  return useMutation<JobAccepted, ApiError, RebuildRequest>({
    mutationFn: (body) =>
      apiFetch<JobAccepted>(`/api/projects/${requireProjectId(projectId)}/feed/rebuild`, { method: "POST", json: body }),
    onSuccess: ({ job }) => {
      queryClient.setQueryData(qk.job(job.id), job);
      if (!isValidId(projectId)) return;
      void invalidateAll(queryClient, [qk.feed(projectId), qk.jobs()]);
    },
  });
}

export interface ExpandVariables {
  paperId: number;
  projectId?: number | null;
  force?: boolean;
}

/** POST /api/papers/{id}/expand?project_id=&force= → 202 with a job, or 200 with `job: null`. */
export function useExpandPaper(): UseMutationResult<ExpandResponse, ApiError, ExpandVariables> {
  const queryClient = useQueryClient();
  return useMutation<ExpandResponse, ApiError, ExpandVariables>({
    mutationFn: ({ paperId, projectId, force }) =>
      apiFetch<ExpandResponse>(`/api/papers/${paperId}/expand`, {
        method: "POST",
        query: { project_id: isValidId(projectId) ? projectId : null, force: force ? true : null },
      }),
    onSuccess: (result, { paperId }) => {
      if (result.job) queryClient.setQueryData(qk.job(result.job.id), result.job);
      void invalidateAll(queryClient, [qk.paperAll(paperId), qk.neighborhoods(), qk.jobs()]);
    },
  });
}

export interface DisruptionVariables {
  paperId: number;
  force?: boolean;
}

/** POST /api/papers/{id}/disruption?force= → DisruptionDetail (synchronous, cached 30 days). */
export function useComputeDisruption(): UseMutationResult<DisruptionDetail, ApiError, DisruptionVariables> {
  const queryClient = useQueryClient();
  return useMutation<DisruptionDetail, ApiError, DisruptionVariables>({
    mutationFn: ({ paperId, force }) =>
      apiFetch<DisruptionDetail>(`/api/papers/${paperId}/disruption`, { method: "POST", query: { force: force ? true : null } }),
    onSuccess: (detail, { paperId }) => {
      queryClient.setQueriesData<PaperDetail>({ queryKey: qk.paperAll(paperId) }, (paper) =>
        paper ? { ...paper, disruption: detail, cd: detail.cd } : paper,
      );
      void invalidateAll(queryClient, [qk.breakthroughs(paperId), qk.libraries()]);
    },
  });
}

export interface BreakthroughVariables {
  paperId: number;
  force?: boolean;
}

/** POST /api/papers/{id}/breakthroughs?force= → 202 `{ job }` (429 quota, 409 budget spent). */
export function useAnalyzeBreakthroughs(): UseMutationResult<JobAccepted, ApiError, BreakthroughVariables> {
  const queryClient = useQueryClient();
  return useMutation<JobAccepted, ApiError, BreakthroughVariables>({
    mutationFn: ({ paperId, force }) =>
      apiFetch<JobAccepted>(`/api/papers/${paperId}/breakthroughs`, { method: "POST", query: { force: force ? true : null } }),
    onSuccess: ({ job }, { paperId }) => {
      queryClient.setQueryData(qk.job(job.id), job);
      void invalidateAll(queryClient, [qk.breakthroughs(paperId), qk.jobs()]);
    },
  });
}

export interface UploadDocumentVariables {
  file: File;
  /** Upload progress 0..1. */
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

/** POST /api/projects/{id}/documents (multipart `file`) → 202 DocumentAccepted; 409; 413; 415. */
export function useUploadDocument(
  projectId: number | null | undefined,
): UseMutationResult<DocumentAccepted, ApiError, UploadDocumentVariables> {
  const queryClient = useQueryClient();
  return useMutation<DocumentAccepted, ApiError, UploadDocumentVariables>({
    mutationFn: ({ file, onProgress, signal }) =>
      upload<DocumentAccepted>(`/api/projects/${requireProjectId(projectId)}/documents`, file, { onProgress, signal }),
    onSuccess: ({ document, job }) => {
      queryClient.setQueryData(qk.document(document.id), document);
      queryClient.setQueryData(qk.job(job.id), job);
      void invalidateAll(queryClient, [qk.documents(document.project_id), qk.project(document.project_id)]);
    },
  });
}

/** DELETE /api/documents/{id} → DocumentDeleted (409 while uploaded/processing). `mutate(documentId)`. */
export function useDeleteDocument(projectId: number | null | undefined): UseMutationResult<DocumentDeleted, ApiError, number> {
  const queryClient = useQueryClient();
  return useMutation<DocumentDeleted, ApiError, number>({
    mutationFn: (documentId) => apiFetch<DocumentDeleted>(`/api/documents/${documentId}`, { method: "DELETE" }),
    onSuccess: (_result, documentId) => {
      queryClient.removeQueries({ queryKey: qk.document(documentId) });
      const keys: QueryKey[] = [qk.papers(), qk.dashboard("work")];
      if (isValidId(projectId)) keys.push(qk.documents(projectId), qk.project(projectId), qk.feed(projectId));
      else keys.push(["documents"]);
      void invalidateAll(queryClient, keys);
    },
  });
}

/** POST /api/dashboard/summary → 202 `{ job }` (409 not_configured / not_enough_papers, 429). */
export function useRequestSummary(): UseMutationResult<JobAccepted, ApiError, SummaryRequest> {
  const queryClient = useQueryClient();
  return useMutation<JobAccepted, ApiError, SummaryRequest>({
    mutationFn: (body) => apiFetch<JobAccepted>("/api/dashboard/summary", { method: "POST", json: body }),
    onSuccess: ({ job }) => {
      queryClient.setQueryData(qk.job(job.id), job);
      void queryClient.invalidateQueries({ queryKey: qk.dashboard("findings") });
    },
  });
}

/** POST /api/jobs/run → 202 `{ job }` (e.g. "Run daily now": `{ kind: "daily" }`). */
export function useRunJob(): UseMutationResult<JobAccepted, ApiError, RunJobRequest> {
  const queryClient = useQueryClient();
  return useMutation<JobAccepted, ApiError, RunJobRequest>({
    mutationFn: (body) => apiFetch<JobAccepted>("/api/jobs/run", { method: "POST", json: body }),
    onSuccess: ({ job }) => {
      queryClient.setQueryData(qk.job(job.id), job);
      void invalidateAll(queryClient, [qk.jobs(), qk.settings()]);
    },
  });
}

/** PATCH /api/settings (admin). */
export function useUpdateSettings(): UseMutationResult<SystemSettings, ApiError, SettingsUpdate> {
  const queryClient = useQueryClient();
  return useMutation<SystemSettings, ApiError, SettingsUpdate>({
    mutationFn: (body) => apiFetch<SystemSettings>("/api/settings", { method: "PATCH", json: body }),
    onSuccess: (settings) => {
      queryClient.setQueryData(qk.settings(), settings);
    },
  });
}

/** POST /api/settings/embedding/download → 202 `{ job }` (admin; 409 when offline). */
export function useDownloadModel(): UseMutationResult<JobAccepted, ApiError, void> {
  const queryClient = useQueryClient();
  return useMutation<JobAccepted, ApiError, void>({
    mutationFn: () => apiFetch<JobAccepted>("/api/settings/embedding/download", { method: "POST" }),
    onSuccess: ({ job }) => {
      queryClient.setQueryData(qk.job(job.id), job);
      void invalidateAll(queryClient, [qk.settings(), qk.jobs()]);
    },
  });
}

/** GET /api/exports/bibtex?project_id=&states= via `download()` (default: relevant and saved). */
export function useExportBibtex(
  projectId: number | null | undefined,
): UseMutationResult<DownloadResult, ApiError, { states?: readonly LibraryStateFilter[] } | void> {
  return useMutation<DownloadResult, ApiError, { states?: readonly LibraryStateFilter[] } | void>({
    mutationFn: (variables) =>
      download("/api/exports/bibtex", {
        query: { project_id: requireProjectId(projectId), states: normalizeList(variables?.states ?? null) },
      }),
  });
}
