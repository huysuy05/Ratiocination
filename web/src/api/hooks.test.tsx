import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  makeFeed,
  makeJob,
  makeLibraryItem,
  makeMe,
  makePaperDetail,
  makePaperSummary,
  makeProject,
  makeSlate,
} from "../test/fixtures";
import {
  createTestQueryClient,
  deferred,
  errorResponse,
  installTestTransport,
  jsonResponse,
  type Deferred,
  type RecordedRequest,
} from "../test/utils";
import {
  getUndoStackSnapshot,
  mergeLibraryPages,
  qk,
  useFeed,
  useFeedback,
  useJob,
  useLibrary,
  useDeleteProject,
  usePaper,
  useProject,
  useSetRead,
  useSignIn,
  useUndo,
} from "./hooks";
import type { FeedbackResponse, FeedResponse, Job, Me, PaperDetail, UndoResponse } from "./types";

const PROJECT = 1;
const SLATE = 5;

function wrapperFor(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function seededClient(): QueryClient {
  const queryClient = createTestQueryClient();
  queryClient.setQueryData(qk.feed(PROJECT), makeFeed({ slate: makeSlate([11, 12, 13], { id: SLATE }) }));
  queryClient.setQueryData(qk.paper(11, PROJECT), makePaperDetail({ id: 11 }));
  return queryClient;
}

function feedItem(queryClient: QueryClient, paperId: number) {
  return queryClient.getQueryData<FeedResponse>(qk.feed(PROJECT))?.slate?.items.find((item) => item.paper.id === paperId);
}

function feedbackOk(interactionId: number, paperId: number, state: FeedbackResponse["state"]): Response {
  const body: FeedbackResponse = {
    interaction_id: interactionId,
    paper_id: paperId,
    state,
    triggered: { expansion_job_id: null },
  };
  return jsonResponse(body);
}

function undoOk(undone: number, paperId: number, state: UndoResponse["state"], last: number | null): Response {
  const body: UndoResponse = { undone_interaction_id: undone, paper_id: paperId, state, last_interaction_id: last };
  return jsonResponse(body);
}

/** A server whose every response is resolved by the test. */
function manualServer() {
  const pending: { request: RecordedRequest; response: Deferred<Response> }[] = [];
  const { requests } = installTestTransport((request) => {
    const response = deferred<Response>();
    pending.push({ request, response });
    return response.promise;
  });
  const respond = (index: number, response: Response) => {
    const entry = pending[index];
    if (!entry) throw new Error(`no request #${index}`);
    entry.response.resolve(response);
  };
  return { requests, respond };
}

function renderFeedbackHooks(queryClient: QueryClient) {
  return renderHook(() => ({ feedback: useFeedback(PROJECT), undo: useUndo(PROJECT) }), {
    wrapper: wrapperFor(queryClient),
  });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("useFeedback", () => {
  it("applies the state optimistically (feed items + paper detail), then the server's answer", async () => {
    const queryClient = seededClient();
    const server = manualServer();
    const { result } = renderFeedbackHooks(queryClient);

    act(() => {
      result.current.feedback.mutate({
        paper_id: 11,
        action: "relevant",
        slate_id: SLATE,
        context: { source: "deck" },
        exitDirection: "right",
      });
    });

    await waitFor(() => expect(feedItem(queryClient, 11)?.state).toBe("relevant"));
    expect(queryClient.getQueryData<PaperDetail>(qk.paper(11, PROJECT))?.state).toBe("relevant");
    expect(queryClient.getQueryData<FeedResponse>(qk.feed(PROJECT))?.slate?.remaining).toBe(2);
    expect(result.current.undo.canUndo).toBe(true); // pending requests are undoable

    await waitFor(() => expect(server.requests).toHaveLength(1));
    expect(server.requests[0]).toMatchObject({
      method: "POST",
      path: "/api/projects/1/feedback",
      body: { paper_id: 11, action: "relevant", slate_id: SLATE, context: { source: "deck" } },
    });
    expect(server.requests[0]?.body).not.toHaveProperty("exitDirection");

    act(() => server.respond(0, feedbackOk(901, 11, "relevant")));
    await waitFor(() => expect(result.current.feedback.isSuccess).toBe(true));
    expect(feedItem(queryClient, 11)).toMatchObject({ state: "relevant", last_interaction_id: 901 });
    expect(queryClient.getQueryData<PaperDetail>(qk.paper(11, PROJECT))?.last_interaction_id).toBe(901);
    expect(getUndoStackSnapshot(PROJECT)).toMatchObject({
      canUndo: true,
      depth: 1,
      pending: 0,
      top: { interaction_id: 901, paper_id: 11, action: "relevant", exit_direction: "right", slate_id: SLATE, previous_state: null },
    });
    expect(result.current.undo.top?.interaction_id).toBe(901);
  });

  it("sends slate_id null when the action has no slate", async () => {
    const queryClient = seededClient();
    const { requests } = installTestTransport(() => feedbackOk(1, 11, "saved"));
    const { result } = renderFeedbackHooks(queryClient);
    act(() => result.current.feedback.mutate({ paper_id: 11, action: "save" }));
    await waitFor(() => expect(result.current.feedback.isSuccess).toBe(true));
    expect(requests[0]?.body).toEqual({ paper_id: 11, action: "save", slate_id: null });
  });

  it("rolls back and shows an error toast when the request fails", async () => {
    const toastError = vi.spyOn(toast, "error").mockImplementation(() => "toast");
    const queryClient = seededClient();
    const server = manualServer();
    const { result } = renderFeedbackHooks(queryClient);

    act(() => result.current.feedback.mutate({ paper_id: 12, action: "not_relevant", slate_id: SLATE }));
    await waitFor(() => expect(feedItem(queryClient, 12)?.state).toBe("not_relevant"));
    await waitFor(() => expect(server.requests).toHaveLength(1));

    act(() => server.respond(0, errorResponse(404, "not_found", "Paper not found")));
    await waitFor(() => expect(result.current.feedback.isError).toBe(true));
    expect(feedItem(queryClient, 12)?.state).toBeNull();
    expect(queryClient.getQueryData<FeedResponse>(qk.feed(PROJECT))?.slate?.remaining).toBe(3);
    expect(toastError).toHaveBeenCalledWith("Paper not found");
    expect(result.current.undo.canUndo).toBe(false);
  });

  it("serializes requests per project", async () => {
    const queryClient = seededClient();
    const server = manualServer();
    const { result } = renderFeedbackHooks(queryClient);

    act(() => {
      result.current.feedback.mutate({ paper_id: 11, action: "relevant", slate_id: SLATE });
      result.current.feedback.mutate({ paper_id: 12, action: "save", slate_id: SLATE });
    });
    // Both cards update immediately…
    await waitFor(() => expect(feedItem(queryClient, 12)?.state).toBe("saved"));
    expect(feedItem(queryClient, 11)?.state).toBe("relevant");
    // …but only the first POST is in flight.
    await waitFor(() => expect(server.requests).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.requests).toHaveLength(1);
    expect(server.requests[0]?.body).toMatchObject({ paper_id: 11 });

    act(() => server.respond(0, feedbackOk(901, 11, "relevant")));
    await waitFor(() => expect(server.requests).toHaveLength(2));
    expect(server.requests[1]?.body).toMatchObject({ paper_id: 12, action: "save" });
    act(() => server.respond(1, feedbackOk(902, 12, "saved")));
    await waitFor(() => expect(getUndoStackSnapshot(PROJECT).depth).toBe(2));
  });

  it("invalidates project, library, project graph, neighborhoods and the dashboard on settle (not the feed)", async () => {
    const queryClient = seededClient();
    installTestTransport(() => feedbackOk(5, 11, "saved"));
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderFeedbackHooks(queryClient);

    act(() => result.current.feedback.mutate({ paper_id: 11, action: "save", slate_id: SLATE }));
    await waitFor(() => expect(result.current.feedback.isSuccess).toBe(true));
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toEqual(
      expect.arrayContaining([["project", 1], ["library", 1], ["projectGraph", 1], ["neighborhood"], ["dashboard"]]),
    );
    expect(keys).not.toContainEqual(["feed", 1]);
  });

  it("never strands a paper detail whose first load is still in flight", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(qk.feed(PROJECT), makeFeed({ slate: makeSlate([11], { id: SLATE }) }));
    const firstLoad = deferred<Response>();
    let detailCalls = 0;
    installTestTransport((request) => {
      if (request.path === "/api/papers/11") {
        detailCalls += 1;
        return detailCalls === 1 ? firstLoad.promise : jsonResponse(makePaperDetail({ id: 11, state: "saved" }));
      }
      return feedbackOk(910, 11, "saved");
    });
    const { result } = renderHook(() => ({ paper: usePaper(11, PROJECT), feedback: useFeedback(PROJECT) }), {
      wrapper: wrapperFor(queryClient),
    });
    await waitFor(() => expect(detailCalls).toBe(1));

    act(() => result.current.feedback.mutate({ paper_id: 11, action: "save", slate_id: SLATE }));
    await waitFor(() => expect(result.current.feedback.isSuccess).toBe(true));
    firstLoad.resolve(jsonResponse(makePaperDetail({ id: 11, state: null })));

    await waitFor(() => expect(result.current.paper.data?.state).toBe("saved"));
    expect(result.current.paper.isPending).toBe(false);
  });

  it("posts `open` without optimistic state, undo entry, toast or invalidation", async () => {
    const toastError = vi.spyOn(toast, "error").mockImplementation(() => "toast");
    const queryClient = seededClient();
    const { requests } = installTestTransport(() => errorResponse(404, "not_found", "Unknown paper"));
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderFeedbackHooks(queryClient);

    act(() => result.current.feedback.mutate({ paper_id: 11, action: "open", slate_id: SLATE, context: { source: "detail" } }));
    await waitFor(() => expect(result.current.feedback.isError).toBe(true));
    expect(requests[0]?.body).toMatchObject({ action: "open" });
    expect(feedItem(queryClient, 11)?.state).toBeNull();
    expect(getUndoStackSnapshot(PROJECT).canUndo).toBe(false);
    expect(toastError).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("useUndo", () => {
  /** Server that answers feedback with increasing interaction ids and undo with state null. */
  function autoServer() {
    let nextInteraction = 900;
    return installTestTransport((request) => {
      const body = request.body as { paper_id?: number; action?: string; interaction_id?: number };
      if (request.path.endsWith("/feedback/undo")) {
        return undoOk(body.interaction_id ?? 0, body.interaction_id === 902 ? 12 : 11, null, null);
      }
      nextInteraction += 1;
      return feedbackOk(nextInteraction, body.paper_id ?? 0, body.action === "save" ? "saved" : "relevant");
    });
  }

  it("pops the stack in LIFO order, always sending interaction_id, and applies the returned state", async () => {
    const queryClient = seededClient();
    const { requests } = autoServer();
    const { result } = renderFeedbackHooks(queryClient);

    act(() => {
      result.current.feedback.mutate({ paper_id: 11, action: "relevant", slate_id: SLATE, exitDirection: "right" });
      result.current.feedback.mutate({ paper_id: 12, action: "save", slate_id: SLATE, exitDirection: "up" });
    });
    await waitFor(() => expect(getUndoStackSnapshot(PROJECT).depth).toBe(2));

    let undone: Awaited<ReturnType<typeof result.current.undo.mutateAsync>> = null;
    await act(async () => {
      undone = await result.current.undo.mutateAsync();
    });
    expect(requests.at(-1)).toMatchObject({ path: "/api/projects/1/feedback/undo", body: { interaction_id: 902 } });
    expect(undone).toMatchObject({ undone_interaction_id: 902, paper_id: 12, entry: { exit_direction: "up", paper_id: 12 } });
    expect(feedItem(queryClient, 12)).toMatchObject({ state: null, last_interaction_id: null });
    expect(result.current.undo.canUndo).toBe(true);

    await act(async () => {
      await result.current.undo.mutateAsync();
    });
    expect(requests.at(-1)?.body).toEqual({ interaction_id: 901 });
    await waitFor(() => expect(result.current.undo.canUndo).toBe(false));

    // Nothing left: resolves null without a request.
    const count = requests.length;
    await act(async () => {
      await expect(result.current.undo.mutateAsync()).resolves.toBeNull();
    });
    expect(requests).toHaveLength(count);
  });

  it("waits for a pending feedback POST and then undoes that action", async () => {
    const queryClient = seededClient();
    const server = manualServer();
    const { result } = renderFeedbackHooks(queryClient);

    act(() => result.current.feedback.mutate({ paper_id: 11, action: "not_relevant", slate_id: SLATE, exitDirection: "left" }));
    await waitFor(() => expect(server.requests).toHaveLength(1));
    act(() => result.current.undo.mutate());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.requests).toHaveLength(1); // the undo waits

    act(() => server.respond(0, feedbackOk(950, 11, "not_relevant")));
    await waitFor(() => expect(server.requests).toHaveLength(2));
    expect(server.requests[1]).toMatchObject({ path: "/api/projects/1/feedback/undo", body: { interaction_id: 950 } });
    act(() => server.respond(1, undoOk(950, 11, null, null)));
    await waitFor(() => expect(result.current.undo.isSuccess).toBe(true));
    expect(result.current.undo.data?.entry?.exit_direction).toBe("left");
    expect(feedItem(queryClient, 11)?.state).toBeNull();
  });

  it("does nothing when the pending action it targets fails (never undoes an older action)", async () => {
    vi.spyOn(toast, "error").mockImplementation(() => "toast");
    const queryClient = seededClient();
    const server = manualServer();
    const { result } = renderFeedbackHooks(queryClient);

    act(() => result.current.feedback.mutate({ paper_id: 11, action: "relevant", slate_id: SLATE }));
    await waitFor(() => expect(server.requests).toHaveLength(1));
    act(() => server.respond(0, feedbackOk(901, 11, "relevant")));
    await waitFor(() => expect(getUndoStackSnapshot(PROJECT).depth).toBe(1));

    act(() => result.current.feedback.mutate({ paper_id: 12, action: "save", slate_id: SLATE }));
    await waitFor(() => expect(server.requests).toHaveLength(2));
    act(() => result.current.undo.mutate());
    act(() => server.respond(1, errorResponse(500, "internal_error", "boom")));

    await waitFor(() => expect(result.current.undo.isSuccess).toBe(true));
    expect(result.current.undo.data).toBeNull();
    expect(server.requests).toHaveLength(2); // no undo POST
    expect(getUndoStackSnapshot(PROJECT)).toMatchObject({ depth: 1, canUndo: true });
    expect(feedItem(queryClient, 12)?.state).toBeNull();
  });

  it("undoes an explicit interaction id (detail sheet) and can clear the stack", async () => {
    const queryClient = seededClient();
    const { requests } = installTestTransport((request) =>
      request.path.endsWith("/undo") ? undoOk(777, 11, null, null) : feedbackOk(901, 11, "relevant"),
    );
    const { result } = renderFeedbackHooks(queryClient);

    await act(async () => {
      await result.current.undo.mutateAsync({ interaction_id: 777 });
    });
    expect(requests[0]?.body).toEqual({ interaction_id: 777 });
    await waitFor(() => expect(result.current.undo.isSuccess).toBe(true));
    expect(result.current.undo.data).toMatchObject({ undone_interaction_id: 777, entry: null });

    act(() => result.current.feedback.mutate({ paper_id: 11, action: "relevant", slate_id: SLATE }));
    await waitFor(() => expect(result.current.undo.canUndo).toBe(true));
    act(() => result.current.undo.clear());
    expect(result.current.undo.canUndo).toBe(false);
  });
});

describe("useJob", () => {
  it("polls until the job settles, calls onSettled once and invalidates by kind", async () => {
    const queryClient = createTestQueryClient();
    const statuses: Job["status"][] = ["queued", "running", "succeeded"];
    let call = 0;
    const { requests } = installTestTransport(() => {
      const status = statuses[Math.min(call, statuses.length - 1)] ?? "succeeded";
      call += 1;
      return jsonResponse(makeJob({ id: 3, kind: "build_deck", project_id: PROJECT, status }));
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const onSettled = vi.fn();
    const { result } = renderHook(() => useJob(3, { intervalMs: 10, onSettled }), { wrapper: wrapperFor(queryClient) });

    await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
    expect(onSettled.mock.calls[0]?.[0]).toMatchObject({ id: 3, status: "succeeded" });
    expect(result.current.data?.status).toBe("succeeded");
    expect(requests.every((request) => request.path === "/api/jobs/3")).toBe(true);
    const settledAt = requests.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(requests).toHaveLength(settledAt); // polling stopped
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual(
      expect.arrayContaining([["jobs"], ["feed", 1], ["project", 1]]),
    );
  });

  it("does nothing without a job id", () => {
    const { requests } = installTestTransport(() => jsonResponse(makeJob()));
    const { result } = renderHook(() => useJob(null), { wrapper: wrapperFor(createTestQueryClient()) });
    expect(result.current.fetchStatus).toBe("idle");
    expect(requests).toHaveLength(0);
  });
});

describe("useFeed", () => {
  it("polls every 1.5 s while the deck is building", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const queryClient = createTestQueryClient();
    let call = 0;
    const { requests } = installTestTransport(() => {
      call += 1;
      return jsonResponse(makeFeed(call < 2 ? { status: "building", job: makeJob({ status: "running" }) } : {}));
    });
    const { result } = renderHook(() => useFeed(PROJECT), { wrapper: wrapperFor(queryClient) });
    await waitFor(() => expect(result.current.data?.status).toBe("building"));
    expect(requests).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    await waitFor(() => expect(result.current.data?.status).toBe("ready"));
    expect(requests).toHaveLength(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(requests).toHaveLength(2);
  });
});

describe("useLibrary", () => {
  it("pages with cursors and merges pages into one LibraryPage", async () => {
    const queryClient = createTestQueryClient();
    const { requests } = installTestTransport((request) =>
      request.query.get("cursor") === "c2"
        ? jsonResponse({ items: [makeLibraryItem({ paper: makePaperSummary({ id: 2 }) })], next_cursor: null, total: 2 })
        : jsonResponse({ items: [makeLibraryItem({ paper: makePaperSummary({ id: 1 }) })], next_cursor: "c2", total: 2 }),
    );
    const { result } = renderHook(() => useLibrary(PROJECT, { states: ["saved", "relevant"], read: "unread", limit: 1 }), {
      wrapper: wrapperFor(queryClient),
    });
    await waitFor(() => expect(result.current.data?.items).toHaveLength(1));
    expect(result.current.hasNextPage).toBe(true);
    expect(requests[0]?.url).toBe("/api/papers?project_id=1&states=relevant%2Csaved&read=unread&limit=1");
    await act(async () => {
      await result.current.fetchNextPage();
    });
    await waitFor(() => expect(result.current.data?.items.map((item) => item.paper.id)).toEqual([1, 2]));
    expect(result.current.data?.total).toBe(2);
    expect(result.current.hasNextPage).toBe(false);
    expect(requests[1]?.query.get("cursor")).toBe("c2");
  });
});

describe("qk", () => {
  it("keeps resource prefixes separate", () => {
    expect(qk.project(1)).toEqual(["project", 1]);
    expect(qk.feed(1)).toEqual(["feed", 1]);
    expect(qk.projects()).toEqual(["projects"]);
    expect(qk.paper(4, null)).toEqual(["paper", 4, null]);
    expect(qk.paper(4, 2).slice(0, 2)).toEqual(qk.paperAll(4));
    expect(qk.me()).toEqual(["me"]);
    expect(qk.settings()).toEqual(["settings"]);
  });

  it("normalizes parameters so equivalent queries share a key", () => {
    const a = qk.library(1, { states: ["saved", "relevant"], query: "  graphs " });
    const b = qk.library(1, { states: ["relevant", "saved", "saved"], query: "graphs", read: "any" });
    expect(a).toEqual(b);
    expect(a.slice(0, 2)).toEqual(qk.libraryAll(1));
    expect(qk.neighborhood(1, null)).toEqual(["neighborhood", 1, null]);
    expect(qk.neighborhood(1, 2).slice(0, 1)).toEqual(qk.neighborhoods());
  });

  it("scopes dashboard keys by section and parameters", () => {
    const params = { days: 30 as const, projectId: null, tz: "UTC" };
    expect(qk.dashboard()).toEqual(["dashboard"]);
    expect(qk.dashboard("topics")).toEqual(["dashboard", "topics"]);
    expect(qk.dashboard("topics", params)).toEqual(["dashboard", "topics", { days: 30, project_id: null, tz: "UTC" }]);
    expect(qk.dashboard("topics", { ...params, projectId: 3 }).slice(0, 2)).toEqual(qk.dashboard("topics"));
  });

  it("returns the bare prefix for default parameters", () => {
    expect(qk.jobs()).toEqual(["jobs"]);
    expect(qk.jobs({ limit: 20 })).toEqual(["jobs"]);
    expect(qk.jobs({ kind: "daily", project_id: 2 })).toEqual(["jobs", { kind: "daily", project_id: 2 }]);
    expect(qk.projectGraph(1)).toEqual(["projectGraph", 1]);
    expect(qk.projectGraph(1, 300)).toEqual(["projectGraph", 1, { limit: 300 }]);
  });
});

describe("page merging", () => {
  it("merges library pages without duplicates", () => {
    const item = makeLibraryItem();
    expect(mergeLibraryPages([])).toEqual({ items: [], total: 0, next_cursor: null });
    expect(
      mergeLibraryPages([
        { items: [item], next_cursor: "x", total: 1 },
        { items: [item], next_cursor: null, total: 1 },
      ]),
    ).toEqual({ items: [item], next_cursor: null, total: 1 });
  });
});

describe("useSetRead", () => {
  it("marks a paper read everywhere it is cached, then keeps the server's timestamp", async () => {
    const queryClient = seededClient();
    const server = manualServer();
    const { result } = renderHook(() => useSetRead(), { wrapper: wrapperFor(queryClient) });

    act(() => result.current.mutate({ paperId: 11, read: true }));
    await waitFor(() => expect(feedItem(queryClient, 11)?.paper.read_at).not.toBeNull());
    expect(queryClient.getQueryData<PaperDetail>(qk.paper(11, PROJECT))?.read_at).not.toBeNull();
    await waitFor(() => expect(server.requests).toHaveLength(1));
    expect(server.requests[0]).toMatchObject({ method: "PUT", path: "/api/papers/11/read", body: { read: true } });

    act(() => server.respond(0, jsonResponse({ paper_id: 11, read_at: "2026-10-12T09:30:00Z" })));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(feedItem(queryClient, 11)?.paper.read_at).toBe("2026-10-12T09:30:00Z");
  });

  it("rolls the mark back when the request fails", async () => {
    vi.spyOn(toast, "error").mockImplementation(() => "toast");
    const queryClient = seededClient();
    installTestTransport(() => errorResponse(404, "not_found", "Paper not found"));
    const { result } = renderHook(() => useSetRead(), { wrapper: wrapperFor(queryClient) });
    act(() => result.current.mutate({ paperId: 11, read: true }));
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(feedItem(queryClient, 11)?.paper.read_at).toBeNull();
    expect(queryClient.getQueryData<PaperDetail>(qk.paper(11, PROJECT))?.read_at).toBeNull();
  });
});

describe("useSignIn", () => {
  it("drops every cached query of the previous session and stores the new user", async () => {
    const queryClient = seededClient();
    queryClient.setQueryData(qk.me(), makeMe({ user: { ...makeMe().user, id: 7, email: "previous@example.org" } }));
    const me = makeMe();
    const { requests } = installTestTransport(() => jsonResponse(me));
    const { result } = renderHook(() => useSignIn(), { wrapper: wrapperFor(queryClient) });

    await act(async () => {
      await result.current.mutateAsync({ email: "test-user@example.org", password: "correct horse 1" });
    });
    expect(requests[0]).toMatchObject({ method: "POST", path: "/api/auth/signin" });
    expect(queryClient.getQueryData(qk.feed(PROJECT))).toBeUndefined();
    expect(queryClient.getQueryData(qk.paper(11, PROJECT))).toBeUndefined();
    expect(queryClient.getQueryData<Me>(qk.me())?.user.email).toBe("test-user@example.org");
  });
});

describe("useDeleteProject", () => {
  it("stops every query of the deleted project at once (no refetch into 404s)", async () => {
    const queryClient = createTestQueryClient();
    let projectCalls = 0;
    installTestTransport((request) => {
      if (request.method === "DELETE") return new Response(null, { status: 204 });
      if (request.path === "/api/projects/9") {
        projectCalls += 1;
        return projectCalls === 1 ? jsonResponse(makeProject({ id: 9 })) : errorResponse(404, "not_found", "Project not found");
      }
      return jsonResponse([]);
    });
    const { result } = renderHook(() => ({ project: useProject(9), remove: useDeleteProject() }), { wrapper: wrapperFor(queryClient) });
    await waitFor(() => expect(result.current.project.data?.id).toBe(9));
    await act(async () => {
      await result.current.remove.mutateAsync(9);
    });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(projectCalls).toBe(1);
    expect(result.current.project.fetchStatus).toBe("idle");
  });
});
