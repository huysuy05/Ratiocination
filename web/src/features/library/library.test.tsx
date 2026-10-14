import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import LibraryPage from "../../pages/LibraryPage";
import { makeLibraryItem, makePaperDetail, makePaperSummary, makeProject } from "../../test/fixtures";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders, type RecordedRequest } from "../../test/utils";

function libraryServer(requests: RecordedRequest[] = []) {
  return (request: RecordedRequest) => {
    requests.push(request);
    if (request.path === "/api/projects/1") {
      return jsonResponse(makeProject({ stats: { ...makeProject().stats, relevant: 2, saved: 1 } }));
    }
    if (request.path === "/api/papers") {
      const states = request.query.get("states");
      const items =
        states === "saved"
          ? [makeLibraryItem({ paper: makePaperSummary({ id: 3, title: "Saved test paper" }), state: "saved" })]
          : [
              makeLibraryItem({ paper: makePaperSummary({ id: 1, title: "Relevant test paper" }), state: "relevant" }),
              makeLibraryItem({ paper: makePaperSummary({ id: 3, title: "Saved test paper" }), state: "saved" }),
            ];
      return jsonResponse({ items: request.query.get("query") === "nothing" ? [] : items, next_cursor: null, total: items.length });
    }
    if (request.path.endsWith("/read")) return jsonResponse({ paper_id: 1, read_at: "2026-10-12T10:00:00Z" });
    if (request.path === "/api/papers/1") return jsonResponse(makePaperDetail({ id: 1 }));
    if (request.path.endsWith("/breakthroughs")) return jsonResponse({ status: "none", job: null, analyzed_at: null, paper: null, scholars: [], message: null });
    if (request.path === "/api/projects/1/feedback") return jsonResponse({ interaction_id: 1, paper_id: 1, state: null, triggered: { expansion_job_id: null } });
    return errorResponse(404, "not_found", `not mocked ${request.path}`);
  };
}

function renderLibrary() {
  return renderWithProviders(null, {
    route: "/projects/1/library",
    routes: [{ path: "/projects/:projectId/library", element: <LibraryPage /> }],
  });
}

describe("LibraryPage", () => {
  it("lists the library and filters by tab, read status and search", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(libraryServer(requests));
    const { user } = renderLibrary();
    expect(await screen.findByRole("button", { name: "Relevant test paper" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Saved test paper" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Saved/ }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Relevant test paper" })).toBeNull());
    expect(requests.some((request) => request.path === "/api/papers" && request.query.get("states") === "saved")).toBe(true);

    await user.click(screen.getByRole("radio", { name: "Unread" }));
    await waitFor(() =>
      expect(requests.some((request) => request.path === "/api/papers" && request.query.get("read") === "unread")).toBe(true),
    );

    await user.type(screen.getByRole("textbox", { name: "Search the library" }), "nothing");
    expect(await screen.findByRole("heading", { name: "No paper matches" })).toBeInTheDocument();
    expect(requests.some((request) => request.query.get("query") === "nothing")).toBe(true);
  });

  it("toggles the read mark and opens a paper in the sheet", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(libraryServer(requests));
    const { user, router } = renderLibrary();
    const marks = await screen.findAllByRole("button", { name: "Mark as read" });
    await user.click(marks[0]!);
    await waitFor(() => expect(requests.some((request) => request.method === "PUT" && request.path === "/api/papers/1/read")).toBe(true));
    await user.click(screen.getByRole("button", { name: "Relevant test paper" }));
    await waitFor(() => expect(router.state.location.search).toBe("?paper=1"));
    expect(await screen.findByRole("dialog", { name: "Test paper A" })).toBeInTheDocument();
  });

  it("explains an empty library", async () => {
    installTestTransport((request) =>
      request.path === "/api/papers" ? jsonResponse({ items: [], next_cursor: null, total: 0 }) : libraryServer()(request),
    );
    renderLibrary();
    expect(await screen.findByRole("heading", { name: "Your library is empty" })).toBeInTheDocument();
  });
});
