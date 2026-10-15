import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { MotionGlobalConfig } from "motion/react";
import { describe, expect, it, onTestFinished } from "vitest";
import type { FeedbackRequest, FeedResponse } from "../../api/types";
import FeedPage from "../../pages/FeedPage";
import { makeFeed, makeJob, makePaperDetail, makeProject, makeSlate } from "../../test/fixtures";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders, type RecordedRequest } from "../../test/utils";
import { dragDecision } from "./SwipeCard";

describe("dragDecision", () => {
  it.each([
    [{ x: 160, y: 10 }, { x: 0, y: 0 }, "relevant"],
    [{ x: -160, y: -20 }, { x: 0, y: 0 }, "not_relevant"],
    [{ x: 20, y: -150 }, { x: 0, y: 0 }, "save"],
    [{ x: 40, y: 5 }, { x: 700, y: 0 }, "relevant"],
    [{ x: -10, y: -30 }, { x: 0, y: -800 }, "save"],
    [{ x: 60, y: -40 }, { x: 100, y: -100 }, null],
  ] as const)("offset %o velocity %o → %s", (offset, velocity, expected) => {
    expect(dragDecision(offset, velocity)).toBe(expected);
  });
});

/** A feed server whose state follows the posted feedback (like the backend). */
function deckServer(initial: FeedResponse = makeFeed({ slate: makeSlate([11, 12, 13], { id: 7 }) })) {
  let feed = initial;
  let nextInteraction = 500;
  const handler = (request: RecordedRequest) => {
    if (request.path === "/api/projects/1/feed") return jsonResponse(feed);
    if (request.path === "/api/projects/1") return jsonResponse(makeProject());
    if (request.path === "/api/projects/1/feedback") {
      const body = request.body as FeedbackRequest;
      nextInteraction += 1;
      const state = body.action === "save" ? "saved" : body.action === "open" ? null : body.action;
      if (feed.slate && state) {
        feed = {
          ...feed,
          slate: {
            ...feed.slate,
            items: feed.slate.items.map((item) => (item.paper.id === body.paper_id ? { ...item, state, last_interaction_id: nextInteraction } : item)),
          },
        };
      }
      return jsonResponse({ interaction_id: nextInteraction, paper_id: body.paper_id, state, triggered: { expansion_job_id: null } });
    }
    if (request.path === "/api/projects/1/feedback/undo") {
      const id = (request.body as { interaction_id: number }).interaction_id;
      const item = feed.slate?.items.find((i) => i.last_interaction_id === id);
      if (feed.slate && item) {
        feed = { ...feed, slate: { ...feed.slate, items: feed.slate.items.map((i) => (i === item ? { ...i, state: null, last_interaction_id: null } : i)) } };
      }
      return jsonResponse({ undone_interaction_id: id, paper_id: item?.paper.id ?? 0, state: null, last_interaction_id: null });
    }
    if (request.path.startsWith("/api/papers/") && request.path.endsWith("/read")) {
      const read = (request.body as { read: boolean }).read;
      return jsonResponse({ paper_id: Number(request.path.split("/")[3]), read_at: read ? "2026-10-12T10:00:00Z" : null });
    }
    if (request.path.startsWith("/api/papers/")) return jsonResponse(makePaperDetail({ id: Number(request.path.split("/")[3]) }));
    if (request.path === "/api/projects/1/feed/rebuild") return jsonResponse({ job: makeJob({ id: 9 }) }, 202);
    return errorResponse(404, "not_found", `not mocked: ${request.path}`);
  };
  return handler;
}

function renderFeed() {
  return renderWithProviders(null, {
    route: "/projects/1/feed",
    routes: [
      { path: "/projects/:projectId/feed", element: <FeedPage /> },
      { path: "/projects/:projectId/settings", element: <h1>Settings page</h1> },
    ],
  });
}

const topCard = () => screen.getByRole("article", { name: /^Card \d+ of \d+/ });

describe("FeedPage", () => {
  it("rates the top card with the arrow keys and moves to the next one", async () => {
    const { requests } = installTestTransport(deckServer());
    renderFeed();
    await waitFor(() => expect(topCard()).toHaveAccessibleName("Card 1 of 3: Test paper K"));
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    await waitFor(() => expect(topCard()).toHaveAccessibleName("Card 2 of 3: Test paper L"));
    const post = requests.find((request) => request.path === "/api/projects/1/feedback");
    expect(post?.body).toEqual({ paper_id: 11, action: "relevant", slate_id: 7, context: { source: "deck" } });
    expect(screen.getByRole("img", { name: "1 of 3 papers decided" })).toBeInTheDocument();
  });

  it("keeps working card after card, with keys and buttons alike", async () => {
    // Real animations: a leaving card stays mounted during its exit, as in a browser.
    MotionGlobalConfig.skipAnimations = false;
    onTestFinished(() => {
      MotionGlobalConfig.skipAnimations = true;
    });
    const { requests } = installTestTransport(deckServer());
    const { user } = renderFeed();
    await waitFor(() => expect(topCard()).toHaveAccessibleName(/Test paper K/));
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    await waitFor(() => expect(topCard()).toHaveAccessibleName(/Test paper L/));
    await user.click(screen.getByRole("button", { name: "Not relevant" }));
    await waitFor(() => expect(topCard()).toHaveAccessibleName(/Test paper M/));
    fireEvent.keyDown(document.body, { key: "ArrowUp" });
    await waitFor(() => expect(screen.getByRole("heading", { name: "Deck complete" })).toBeInTheDocument());
    const actions = requests
      .filter((request) => request.path === "/api/projects/1/feedback")
      .map((request) => [(request.body as FeedbackRequest).paper_id, (request.body as FeedbackRequest).action]);
    expect(actions).toEqual([
      [11, "relevant"],
      [12, "not_relevant"],
      [13, "save"],
    ]);
  });

  it("uses the buttons too, and Undo brings the card back", async () => {
    const { requests } = installTestTransport(deckServer());
    const { user } = renderFeed();
    await waitFor(() => expect(topCard()).toHaveAccessibleName(/Test paper K/));
    await user.click(screen.getByRole("button", { name: "Not relevant" }));
    await waitFor(() => expect(topCard()).toHaveAccessibleName(/Test paper L/));
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(topCard()).toHaveAccessibleName(/Test paper K/));
    expect(requests.find((request) => request.path.endsWith("/feedback/undo"))?.body).toEqual({ interaction_id: 501 });
  });

  it("saves with S and toggles the read mark with R", async () => {
    const { requests } = installTestTransport(deckServer());
    renderFeed();
    await waitFor(() => expect(topCard()).toHaveAccessibleName(/Test paper K/));
    fireEvent.keyDown(document.body, { key: "r" });
    await waitFor(() => expect(requests.some((request) => request.path === "/api/papers/11/read")).toBe(true));
    expect(requests.find((request) => request.path === "/api/papers/11/read")?.body).toEqual({ read: true });
    await waitFor(() => expect(screen.getByRole("button", { name: "Mark unread" })).toBeInTheDocument());
    fireEvent.keyDown(document.body, { key: "s" });
    await waitFor(() => expect(requests.find((request) => request.path === "/api/projects/1/feedback")?.body).toMatchObject({ action: "save" }));
  });

  it("ends with the deck-complete summary", async () => {
    const slate = makeSlate([11, 12], { id: 7 });
    slate.items = slate.items.map((item, index) => ({ ...item, state: index === 0 ? "relevant" : "not_relevant" }));
    installTestTransport(deckServer(makeFeed({ slate })));
    renderFeed();
    const heading = await screen.findByRole("heading", { name: "Deck complete" });
    expect(heading).toBeInTheDocument();
    const summary = heading.closest("div")?.parentElement ?? document.body;
    expect(within(summary).getByRole("link", { name: "Explore connections" })).toHaveAttribute("href", "/projects/1/graph");  // the whole project graph, not one paper's neighbourhood
  });

  it("asks for categories when the project has none", async () => {
    installTestTransport(deckServer(makeFeed({ status: "needs_setup", slate: null })));
    renderFeed();
    expect(await screen.findByRole("link", { name: "Add categories" })).toHaveAttribute("href", "/projects/1/settings");
  });

  it("shows the radar sweep while the first deck builds", async () => {
    installTestTransport(deckServer(makeFeed({ status: "building", slate: null, job: makeJob({ status: "running", message: "Embedding 412 papers", progress: 0.4 }) })));
    renderFeed();
    expect(await screen.findByText("Embedding 412 papers")).toBeInTheDocument();
    expect(screen.getByText("Building your deck")).toBeInTheDocument();
  });

  it("builds the first deck on request", async () => {
    const { requests } = installTestTransport(deckServer(makeFeed({ status: "not_built", slate: null })));
    const { user } = renderFeed();
    await user.click(await screen.findByRole("button", { name: "Build my first deck" }));
    await waitFor(() => expect(requests.find((request) => request.path.endsWith("/feed/rebuild"))?.body).toEqual({ mode: "daily", prepare: true }));
  });

  it("shows a failed build with Retry", async () => {
    installTestTransport(
      deckServer(makeFeed({ last_job: makeJob({ status: "failed", error_message: "arXiv was unavailable" }) })),
    );
    renderFeed();
    expect(await screen.findByText(/The last deck build failed: arXiv was unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("opens the detail sheet with Enter", async () => {
    installTestTransport(deckServer());
    const { router } = renderFeed();
    await waitFor(() => expect(topCard()).toBeInTheDocument());
    fireEvent.keyDown(document.body, { key: "Enter" });
    await waitFor(() => expect(router.state.location.search).toBe("?paper=11"));
  });
});
