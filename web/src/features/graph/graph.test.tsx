import { render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { Toaster } from "sonner";
import { describe, expect, it } from "vitest";
import type { Neighborhood, ProjectGraph } from "../../api/types";
import GraphPage from "../../pages/GraphPage";
import { getCytoscapeInstances, lastCytoscapeInstance } from "../../test/cytoscape";
import { makeGraphEdge, makeGraphNode, makeNeighborhood, makePaperDetail, makePaperSummary, makeProject } from "../../test/fixtures";
import { setMatchMedia } from "../../test/matchMedia";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders, type RecordedRequest } from "../../test/utils";
import { GraphCanvas } from "./GraphCanvas";
import { describeEdge, relationCounts, workPaperId } from "./model";
import { nodeSize, shortLabel } from "./style";

const center = makeGraphNode({ id: "work:1", is_center: true, paper: makePaperSummary({ id: 1, title: "Center test paper" }) });
const cited = makeGraphNode({ id: "work:2", paper: makePaperSummary({ id: 2, title: "Cited test paper" }), state: "relevant" });
const topic = makeGraphNode({ id: "concept:5", type: "concept", label: "Test topic", paper: null });
const NEIGHBORHOOD: Neighborhood = makeNeighborhood({
  center: "work:1",
  nodes: [center, cited, topic],
  edges: [
    makeGraphEdge({ id: "e:1", source: "work:1", target: "work:2", relation: "CITES", evidence: { relation: "CITES", contexts: ["as shown by prior work"], intents: ["background"], is_influential: null } }),
    makeGraphEdge({ id: "e:2", source: "work:1", target: "concept:5", relation: "HAS_TOPIC", verified: true, provenance: "openalex", evidence: null }),
  ],
  groups: [
    { key: "references", label: "References", node_ids: ["work:2"], count: 3 },
    { key: "topics", label: "Topics", node_ids: ["concept:5"], count: 1 },
  ],
  notices: ["Semantic Scholar was unavailable; citations come from OpenAlex"],
});

function server(requests: RecordedRequest[] = []) {
  return (request: RecordedRequest) => {
    requests.push(request);
    if (request.path === "/api/projects/1") return jsonResponse(makeProject());
    if (request.path === "/api/papers/1/neighborhood") return jsonResponse(NEIGHBORHOOD);
    if (request.path === "/api/projects/1/graph") {
      const graph: ProjectGraph = { nodes: [], edges: [], groups: [], notices: [] };
      return jsonResponse(graph);
    }
    if (request.path === "/api/papers/1/expand") {
      return jsonResponse({ job: null, expansion: NEIGHBORHOOD.expansion, message: "Citations were loaded in the last 7 days" });
    }
    if (request.path === "/api/papers/2") return jsonResponse(makePaperDetail({ id: 2 }));
    if (request.path.endsWith("/breakthroughs")) return jsonResponse({ status: "none", job: null, analyzed_at: null, paper: null, scholars: [], message: null });
    if (request.path.endsWith("/feedback")) return jsonResponse({ interaction_id: 1, paper_id: 2, state: null, triggered: { expansion_job_id: null } });
    return errorResponse(404, "not_found", `not mocked ${request.path}`);
  };
}

function renderGraph(route: string) {
  return renderWithProviders(null, {
    route,
    routes: [
      {
        path: "/projects/:projectId/graph/:paperId?",
        element: (
          <>
            <GraphPage />
            <Toaster />
          </>
        ),
      },
    ],
  });
}

describe("graph helpers", () => {
  it("parses node ids, counts relations and describes links", () => {
    expect(workPaperId("work:12")).toBe(12);
    expect(workPaperId("concept:3")).toBeNull();
    expect(relationCounts(NEIGHBORHOOD.edges)).toEqual({ CITES: 1, SEMANTICALLY_SIMILAR: 0, HAS_TOPIC: 1, AUTHORED: 0 });
    const labelOf = (id: string) => ({ "work:1": "A", "work:2": "B" })[id] ?? id;
    expect(describeEdge(NEIGHBORHOOD.edges[0]!, "work:1", labelOf)).toBe("Cites “B”");
    expect(describeEdge(NEIGHBORHOOD.edges[0]!, "work:2", labelOf)).toBe("Cited by “A”");
    expect(shortLabel("x".repeat(50), 10)).toHaveLength(10);
    expect(nodeSize(makeGraphNode({ weight: 1 }))).toBe(36);
    expect(nodeSize(makeGraphNode({ weight: 0 }))).toBe(14);
  });
});

describe("GraphPage", () => {
  it("draws the neighbourhood on the canvas with notices and relation filters", async () => {
    setMatchMedia({ "(min-width: 768px)": true });
    installTestTransport(server());
    renderGraph("/projects/1/graph/1");
    expect(await screen.findByRole("heading", { level: 1, name: "Center test paper" })).toBeInTheDocument();
    expect(screen.getByText("Semantic Scholar was unavailable; citations come from OpenAlex")).toBeInTheDocument();
    await waitFor(() => expect(lastCytoscapeInstance()?.getElementById("work:2").length).toBe(1));
    expect(lastCytoscapeInstance()?.getElementById("e:1").length).toBe(1);
    expect(screen.getByRole("button", { name: /Citations/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: /Similar/ })).toBeNull();
  });

  it("lays a paper's neighbourhood out as a wheel around it, with a header per group", async () => {
    setMatchMedia({ "(min-width: 768px)": true });
    installTestTransport(server());
    renderGraph("/projects/1/graph/1");
    await waitFor(() => expect(lastCytoscapeInstance()?.layouts.length).toBeGreaterThan(0));
    const cy = lastCytoscapeInstance()!;
    const layout = cy.layouts.at(-1) as { name: string; positions: Record<string, { x: number; y: number }> };
    expect(layout.name).toBe("preset");
    expect(layout.positions["work:1"]).toEqual({ x: 0, y: 0 });
    const header = cy.getElementById("header:references:left");
    expect(header.hasClass("header")).toBe(true);
    expect(header.data("short")).toBe("References · 1 of 3");
    expect(cy.getElementById("work:2").data("side")).toBe("left");
    expect(layout.positions["header:references:left"]).toBeDefined();
  });

  it("shows the selection panel when a node is tapped", async () => {
    setMatchMedia({ "(min-width: 768px)": true });
    installTestTransport(server());
    renderGraph("/projects/1/graph/1");
    await waitFor(() => expect(lastCytoscapeInstance()?.getElementById("work:2").length).toBe(1));
    lastCytoscapeInstance()?.emitEvent("tap", "work:2");
    const panel = await screen.findByRole("complementary", { name: "Selection" });
    expect(within(panel).getByText("Cited test paper")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Center here" })).toBeInTheDocument();
  });

  it("lists the same graph as rows with links and actions", async () => {
    setMatchMedia({ "(min-width: 768px)": false });
    installTestTransport(server());
    const { user, router } = renderGraph("/projects/1/graph/1");
    expect(await screen.findByRole("heading", { name: /References/ })).toHaveTextContent("1 of 3");
    await user.click(screen.getAllByRole("button", { name: /link/ })[0]!);
    expect(await screen.findByText("Cites “Cited test paper”")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Center" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/1/graph/2"));
  });

  it("asks for citations and reports a recent expansion", async () => {
    setMatchMedia({ "(min-width: 768px)": false });
    const requests: RecordedRequest[] = [];
    installTestTransport(server(requests));
    const { user } = renderGraph("/projects/1/graph/1");
    await user.click(await screen.findByRole("button", { name: "Load citations" }));
    await waitFor(() => expect(requests.some((request) => request.method === "POST" && request.path === "/api/papers/1/expand")).toBe(true));
    expect(await screen.findByText("Citations were loaded in the last 7 days")).toBeInTheDocument();
  });

  it("explains an empty project graph", async () => {
    installTestTransport(server());
    renderGraph("/projects/1/graph");
    expect(await screen.findByRole("heading", { name: "Nothing to connect yet" })).toBeInTheDocument();
  });
});

describe("GraphCanvas", () => {
  const nodes = [1, 2, 3, 4].map((id) => makeGraphNode({ id: `work:${id}`, paper: makePaperSummary({ id }) }));
  const edges = [makeGraphEdge({ id: "e:1", source: "work:1", target: "work:2" }), makeGraphEdge({ id: "e:2", source: "work:3", target: "work:2" })];
  const canvas = <GraphCanvas nodes={nodes} edges={edges} hiddenRelations={new Set()} selectedId={null} onSelect={() => {}} />;

  it("lays the project graph out from scratch, nodes never starting on one spot", () => {
    render(canvas);
    const cy = lastCytoscapeInstance()!;
    expect(cy.layouts).toHaveLength(1);
    expect(cy.layouts[0]).toMatchObject({ name: "fcose", randomize: true, fit: true });
    const starts = new Set([...cy.store.values()].filter((e) => e.group === "nodes").map((e) => `${e.position.x},${e.position.y}`));
    expect(starts.size).toBe(nodes.length);
  });

  it("gives the instance StrictMode re-creates a first layout of its own", () => {
    // Development mounts effects twice: the first instance is destroyed and the second starts
    // empty. Re-using the first one's "already laid out" flag stacked every node on one spot and
    // fcose pulled them apart along a single line.
    render(<StrictMode>{canvas}</StrictMode>);
    const instances = getCytoscapeInstances();
    expect(instances).toHaveLength(2);
    expect(instances[0]?.destroyed()).toBe(true);
    expect(instances[1]?.layouts[0]).toMatchObject({ name: "fcose", randomize: true });
    // …and grows out of the middle of the canvas (the fake canvas is 800 × 600).
    expect(instances[1]?.pan).toHaveBeenCalledWith({ x: 400, y: 300 });
  });
});
