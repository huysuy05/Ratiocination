import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Breakthroughs, DisruptionDetail, FeedbackRequest, PaperDetail } from "../../api/types";
import { getActiveHotkeyScope, useHotkeys } from "../../lib/hotkeys";
import { makeJob, makePaperDetail, makePaperSummary, makeRecommendationItem } from "../../test/fixtures";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders, type RecordedRequest } from "../../test/utils";
import { PaperDetailSheet } from "./PaperDetailSheet";

const NONE: Breakthroughs = { status: "none", job: null, analyzed_at: null, paper: null, scholars: [], message: null };

const DISRUPTION: DisruptionDetail = {
  cd: -0.000487,
  n_citing: 468,
  n_i: 174,
  n_j: 294,
  n_k: 245711,
  refs_used: 37,
  refs_total: 37,
  computed_at: "2026-10-12T08:00:00Z",
  label: "consolidating",
  text: "Of 468 citing papers, 174 (37%) cite it without citing its references and 294 cite both; 245,711 other papers cite its references but not it.",
};

function paperServer(paper: PaperDetail, extra?: (request: RecordedRequest) => Response | null) {
  return (request: RecordedRequest) => {
    const answer = extra?.(request);
    if (answer) return answer;
    if (request.path === `/api/papers/${paper.id}`) return jsonResponse(paper);
    if (request.path === `/api/papers/${paper.id}/breakthroughs`) return jsonResponse(NONE);
    if (request.path === "/api/projects/1/feedback") {
      const body = request.body as FeedbackRequest;
      return jsonResponse({
        interaction_id: 77,
        paper_id: body.paper_id,
        state: body.action === "open" ? null : body.action === "save" ? "saved" : body.action,
        triggered: { expansion_job_id: null },
      });
    }
    return errorResponse(404, "not_found", `not mocked: ${request.path}`);
  };
}

function renderSheet(props: { paperId: number | null; projectId: number | null; slateId?: number | null }) {
  const onClose = vi.fn();
  return {
    onClose,
    ...renderWithProviders(<PaperDetailSheet paperId={props.paperId} projectId={props.projectId} slateId={props.slateId} onClose={onClose} />),
  };
}

function Deck({ onLeft }: { onLeft: () => void }) {
  useHotkeys("deck", { ArrowLeft: onLeft });
  return null;
}

describe("PaperDetailSheet", () => {
  it("is closed when paperId is null", () => {
    installTestTransport(() => errorResponse(404, "not_found"));
    renderSheet({ paperId: null, projectId: 1 });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the paper, posts `open` once with the slate, and Close calls onClose", async () => {
    const { requests } = installTestTransport(paperServer(makePaperDetail({ id: 12 })));
    const { user, onClose } = renderSheet({ paperId: 12, projectId: 1, slateId: 5 });
    const dialog = await screen.findByRole("dialog", { name: "Test paper L" });
    // One heading: the dialog title (the visible serif title is not a second heading).
    expect(within(dialog).getAllByRole("heading", { name: "Test paper L" })).toHaveLength(1);
    expect(within(dialog).getByText("Test paper L", { selector: "p" })).toBeVisible();
    await waitFor(() => expect(requests.filter((request) => request.path === "/api/projects/1/feedback")).toHaveLength(1));
    expect(requests.find((request) => request.path === "/api/projects/1/feedback")?.body).toEqual({
      paper_id: 12,
      action: "open",
      slate_id: 5,
      context: { source: "detail" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not post `open` without a project and hides the deck actions", async () => {
    const { requests } = installTestTransport(paperServer(makePaperDetail({ id: 12 })));
    renderSheet({ paperId: 12, projectId: null });
    await screen.findByRole("dialog", { name: "Test paper L" });
    expect(requests.some((request) => request.path.includes("/feedback"))).toBe(false);
    expect(screen.queryByRole("button", { name: "Relevant" })).toBeNull();
  });

  it("closes on Escape", async () => {
    installTestTransport(paperServer(makePaperDetail({ id: 12 })));
    const { onClose, user } = renderSheet({ paperId: 12, projectId: null });
    await screen.findByRole("dialog", { name: "Test paper L" });
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("suspends the deck shortcuts while open", async () => {
    installTestTransport(paperServer(makePaperDetail({ id: 3 })));
    const onLeft = vi.fn();
    function Harness() {
      const [open, setOpen] = useState(true);
      return (
        <>
          <Deck onLeft={onLeft} />
          <PaperDetailSheet paperId={open ? 3 : null} projectId={null} onClose={() => setOpen(false)} />
        </>
      );
    }
    const { user } = renderWithProviders(<Harness />);
    await screen.findByRole("dialog", { name: "Test paper C" });
    expect(getActiveHotkeyScope()).toBe("sheet");
    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    expect(onLeft).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(getActiveHotkeyScope()).toBe("deck");
    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    expect(onLeft).toHaveBeenCalledTimes(1);
  });

  it("decides with the keyboard and closes", async () => {
    const { requests } = installTestTransport(paperServer(makePaperDetail({ id: 12 })));
    const { onClose } = renderSheet({ paperId: 12, projectId: 1, slateId: 5 });
    await screen.findByRole("dialog", { name: "Test paper L" });
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    const actions = requests
      .filter((request) => request.path === "/api/projects/1/feedback")
      .map((request) => (request.body as FeedbackRequest).action);
    expect(actions).toEqual(["open", "relevant"]);
  });

  it("computes the disruption index on request", async () => {
    const { requests } = installTestTransport(
      paperServer(makePaperDetail({ id: 12 }), (request) => (request.path === "/api/papers/12/disruption" ? jsonResponse(DISRUPTION) : null)),
    );
    const { user } = renderSheet({ paperId: 12, projectId: null });
    await user.click(await screen.findByRole("button", { name: "Compute disruption" }));
    expect(await screen.findByText(DISRUPTION.text)).toBeInTheDocument();
    expect(screen.getByText("Consolidating")).toBeInTheDocument();
    expect(requests.find((request) => request.path === "/api/papers/12/disruption")?.method).toBe("POST");
  });

  it("explains a paper that is not in OpenAlex", async () => {
    installTestTransport(
      paperServer(makePaperDetail({ id: 12 }), (request) =>
        request.path === "/api/papers/12/disruption" ? errorResponse(422, "validation_error", "No OpenAlex record") : null,
      ),
    );
    const { user } = renderSheet({ paperId: 12, projectId: null });
    await user.click(await screen.findByRole("button", { name: "Compute disruption" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This paper is not in OpenAlex yet");
  });

  it("starts the breakthrough analysis and shows its progress", async () => {
    let breakthroughs: Breakthroughs = NONE;
    const { requests } = installTestTransport(
      paperServer(makePaperDetail({ id: 12 }), (request) => {
        if (request.path !== "/api/papers/12/breakthroughs") return null;
        if (request.method === "POST") {
          const job = makeJob({ kind: "breakthroughs", status: "running", message: "Analysing author 2 of 5" });
          breakthroughs = { ...NONE, status: "running", job };
          return jsonResponse({ job }, 202);
        }
        return jsonResponse(breakthroughs);
      }),
    );
    const { user } = renderSheet({ paperId: 12, projectId: null });
    await user.click(await screen.findByRole("button", { name: "Find their breakthrough papers" }));
    expect(await screen.findByText("Analysing author 2 of 5")).toBeInTheDocument();
    expect(requests.some((request) => request.method === "POST" && request.path === "/api/papers/12/breakthroughs")).toBe(true);
  });

  it("lists the authors' works by disruption with breakthrough badges", async () => {
    const done: Breakthroughs = {
      status: "done",
      job: null,
      analyzed_at: "2026-10-12T08:00:00Z",
      paper: DISRUPTION,
      message: null,
      scholars: [
        {
          position: 0,
          scholar: {
            id: 4,
            openalex_id: "A1",
            display_name: "Test Scholar",
            orcid: null,
            works_count: 120,
            cited_by_count: 9000,
            h_index: 31,
            institution: "Test University",
            analyzed_at: "2026-10-12T08:00:00Z",
          },
          works: [
            { paper: makePaperSummary({ id: 20, title: "A disruptive test paper" }), cd: 0.41, n_citing: 300, is_breakthrough: true },
            { paper: makePaperSummary({ id: 21, title: "A consolidating test paper" }), cd: -0.12, n_citing: 80, is_breakthrough: false },
          ],
        },
      ],
    };
    installTestTransport(
      paperServer(makePaperDetail({ id: 12 }), (request) => (request.path === "/api/papers/12/breakthroughs" ? jsonResponse(done) : null)),
    );
    renderSheet({ paperId: 12, projectId: null });
    expect(await screen.findByText("1 breakthrough paper among 1 authors.")).toBeInTheDocument();
    expect(screen.getByText("Test Scholar")).toBeInTheDocument();
    expect(screen.getByText("+0.41")).toBeInTheDocument();
    expect(screen.getByText("−0.12")).toBeInTheDocument();
    expect(screen.getByText("Breakthrough")).toBeInTheDocument();
  });

  it("expands a reason to its private draft evidence", async () => {
    const paper = makePaperDetail({
      id: 12,
      recommendation: makeRecommendationItem({
        reasons: [
          {
            code: "draft_match",
            kind: "model",
            text: "Matches your document “thesis.md”, section “Methods”, p. 4",
            evidence: { document_id: 1, chunk_id: 9, filename: "thesis.md", section: "Methods", page: 4, snippet: "We train a sparse encoder on long inputs." },
            contribution: 0.12,
          },
        ],
      }),
    });
    installTestTransport(paperServer(paper));
    const { user } = renderSheet({ paperId: 12, projectId: 1 });
    await user.click(await screen.findByRole("button", { name: /Matches your document/ }));
    expect(await screen.findByText("We train a sparse encoder on long inputs.")).toBeInTheDocument();
    expect(screen.getByText("This passage is private and never leaves the server.")).toBeInTheDocument();
  });

  it("shows which keyword matched where, and the keyword segment of the score breakdown", async () => {
    const recommendation = makeRecommendationItem({
      reasons: [
        {
          code: "keyword",
          kind: "metadata",
          text: "Mentions your keywords ‘conformal prediction’ in the title and ‘RAG’ in the abstract",
          evidence: {
            matches: [
              { keyword: "conformal prediction", field: "title", text: "Conformal Predictions" },
              { keyword: "RAG", field: "abstract", text: "RAG" },
            ],
          },
          contribution: 0.21,
        },
      ],
    });
    recommendation.features.keyword = { value: 0.78, raw: 1.5, weight: 0.2, contribution: 0.21, available: true };
    recommendation.features.draft = { value: null, raw: null, weight: 0.15, contribution: 0, available: false };
    const paper = makePaperDetail({ id: 12, recommendation });
    installTestTransport(paperServer(paper));
    const { user } = renderSheet({ paperId: 12, projectId: 1 });
    const sources = (await screen.findByRole("heading", { name: "From the sources" })).closest("section");
    expect(sources).not.toBeNull();
    await user.click(within(sources as HTMLElement).getByRole("button", { name: /Mentions your keywords/ }));
    expect(await screen.findByText("Conformal Predictions")).toBeInTheDocument();
    expect(screen.getByText(/appears in the title as/)).toBeInTheDocument();
    expect(screen.getByText(/appears in the abstract as/)).toBeInTheDocument();

    const table = screen.getByRole("table", { name: "Feature scores" });
    const rows = within(table).getAllByRole("row").slice(1).map((row) => within(row).getByRole("rowheader").textContent);
    expect(rows).toEqual(["Research question", "Similar to your papers", "Your keywords", "Your draft", "Citation link", "Your ratings", "Recency"]);
    const keywordRow = within(table).getByRole("rowheader", { name: "Your keywords" }).closest("tr") as HTMLElement;
    expect(within(keywordRow).getByText("1.500")).toBeInTheDocument();
    expect(within(keywordRow).getByText("0.210")).toBeInTheDocument();
    const bar = screen.getByRole("img", { name: /^Score contributions:/ });
    expect(bar.getAttribute("aria-label")).toContain("Your keywords");
    expect(bar.getAttribute("aria-label")).toContain("Your draft (not available)");
  });

  it("reads a deck built before keywords existed", async () => {
    const recommendation = makeRecommendationItem();
    const { keyword: _dropped, ...older } = recommendation.features;
    const paper = makePaperDetail({ id: 12, recommendation: { ...recommendation, features: older as typeof recommendation.features } });
    installTestTransport(paperServer(paper));
    renderSheet({ paperId: 12, projectId: 1 });
    const table = await screen.findByRole("table", { name: "Feature scores" });
    const keywordRow = within(table).getByRole("rowheader", { name: "Your keywords" }).closest("tr") as HTMLElement;
    expect(within(keywordRow).getByText("not available")).toBeInTheDocument();
  });
});
