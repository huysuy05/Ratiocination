import { render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { DashboardFindings } from "../../api/types";
import DashboardPage from "../../pages/DashboardPage";
import { makeActivity, makeFindings, makeMe, makePaperDetail, makeProject, makeTopics, makeWork } from "../../test/fixtures";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders, type RecordedRequest } from "../../test/utils";
import { highlightCues } from "./FindingsSection";
import { countDelta, parseDays, rateDelta } from "./format";

describe("dashboard helpers", () => {
  it("highlights result cues and numbers", () => {
    const { container } = render(<p>{highlightCues("Experiments show that it outperforms baselines by 12% on three tasks.")}</p>);
    expect([...container.querySelectorAll("mark")].map((mark) => mark.textContent)).toEqual(["Experiments show", "outperforms", "12%"]);
  });

  it("formats deltas and parses the period", () => {
    expect(countDelta(12, 8)).toEqual({ text: "+50%", tone: "up" });
    expect(countDelta(6, 8)).toEqual({ text: "−25%", tone: "down" });
    expect(countDelta(3, 0)).toEqual({ text: "new", tone: "up" });
    expect(countDelta(0, 0)).toEqual({ text: "—", tone: "none" });
    expect(rateDelta(0.5833, 0.375)).toEqual({ text: "+21 pts", tone: "up" });
    expect(rateDelta(null, 0.4)).toEqual({ text: "—", tone: "none" });
    expect(parseDays("7")).toBe(7);
    expect(parseDays("14")).toBe(30);
    expect(parseDays(null)).toBe(30);
  });
});

function dashboardServer(requests: RecordedRequest[], findings: DashboardFindings = makeFindings()) {
  return (request: RecordedRequest) => {
    requests.push(request);
    if (request.path === "/api/me") return jsonResponse(makeMe({ user: { ...makeMe().user, preferences: { auto_expand: true, timezone: "Europe/Berlin" } } }));
    if (request.path === "/api/projects") return jsonResponse([makeProject({ id: 1, name: "Test project" }), makeProject({ id: 2, name: "Second project" })]);
    if (request.path === "/api/dashboard/activity") return jsonResponse(makeActivity());
    if (request.path === "/api/dashboard/topics") return jsonResponse(makeTopics());
    if (request.path === "/api/dashboard/findings") return jsonResponse(findings);
    if (request.path === "/api/dashboard/work") {
      const work = makeWork();
      work.projects[0]!.correlation = {
        r: 0.42,
        n: 120,
        strength: "moderate",
        text: "Papers closer to your work were more likely to be marked relevant (r = 0.42, n = 120)",
      };
      return jsonResponse(work);
    }
    if (request.path === "/api/dashboard/summary") return jsonResponse({ job: { ...findings.ai.job, id: 4, kind: "summarize_findings", status: "queued" } }, 202);
    if (/^\/api\/papers\/\d+$/.test(request.path)) return jsonResponse(makePaperDetail({ id: Number(request.path.split("/")[3]) }));
    if (request.path.endsWith("/breakthroughs")) return jsonResponse({ status: "none", job: null, analyzed_at: null, paper: null, scholars: [], message: null });
    return errorResponse(404, "not_found", `not mocked ${request.path}`);
  };
}

function renderDashboard(route = "/dashboard") {
  return renderWithProviders(null, { route, routes: [{ path: "/dashboard", element: <DashboardPage /> }] });
}

describe("DashboardPage", () => {
  it("shows activity, topics, findings and the relation to your work in your time zone", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(dashboardServer(requests));
    renderDashboard();
    const kpis = await screen.findByRole("list", { name: "Key numbers" });
    expect(within(kpis).getByText("Papers swiped")).toBeInTheDocument();
    await waitFor(() => expect(within(kpis).getByText("58%", { selector: ".sr-only" })).toBeInTheDocument());
    expect(screen.getByText("Leaning in")).toBeInTheDocument();
    expect(screen.getAllByText("Machine Learning (cs.LG)").length).toBeGreaterThan(0);
    expect(screen.getByText(/outperforms/)).toBeInTheDocument();
    expect(screen.getByText(/Papers closer to your work were more likely to be marked relevant/)).toBeInTheDocument();
    const activityCall = requests.find((request) => request.path === "/api/dashboard/activity");
    expect(activityCall?.query.get("days")).toBe("30");
    expect(activityCall?.query.get("tz")).toBe("Europe/Berlin");
    expect(activityCall?.query.has("project_id")).toBe(false);
  });

  it("keeps the period and project in the URL and refetches", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(dashboardServer(requests));
    const { user, router } = renderDashboard();
    await screen.findByRole("list", { name: "Key numbers" });
    await user.click(screen.getByRole("radio", { name: "7 days" }));
    await waitFor(() => expect(router.state.location.search).toBe("?days=7"));
    await waitFor(() => expect(requests.some((request) => request.path === "/api/dashboard/topics" && request.query.get("days") === "7")).toBe(true));
    await user.selectOptions(screen.getByRole("combobox", { name: "Project" }), "2");
    await waitFor(() => expect(router.state.location.search).toBe("?days=7&project=2"));
    await waitFor(() => expect(requests.some((request) => request.path === "/api/dashboard/work" && request.query.get("project_id") === "2")).toBe(true));
  });

  it("asks Claude for a summary of the period", async () => {
    const requests: RecordedRequest[] = [];
    const findings = makeFindings({ n_papers: 5, ai: { available: true, model: "claude-opus-5-5", remaining_today: 5, job: null } });
    installTestTransport(dashboardServer(requests, findings));
    const { user } = renderDashboard("/dashboard?days=90&project=1");
    await user.click(await screen.findByRole("button", { name: "Summarize with Claude" }));
    await waitFor(() => expect(requests.some((request) => request.path === "/api/dashboard/summary")).toBe(true));
    expect(requests.find((request) => request.path === "/api/dashboard/summary")?.body).toEqual({ days: 90, project_id: 1, tz: "Europe/Berlin" });
    expect(screen.getByText(/Only public titles and abstracts are sent to Anthropic/)).toBeInTheDocument();
  });

  it("shows a stored summary with its cited papers and a stale badge", async () => {
    const requests: RecordedRequest[] = [];
    const findings = makeFindings({
      ai: { available: true, model: "claude-opus-5-5", remaining_today: 4, job: null },
      summary: {
        id: 1,
        created_at: "2026-10-12T08:00:00Z",
        model: "claude-opus-5-5",
        n_papers: 3,
        themes: [{ title: "Sparse retrieval", points: [{ text: "Sparse models beat dense ones on long inputs.", papers: [{ id: 7, title: "A cited test paper" }] }] }],
        dropped_points: 1,
        stale: true,
      },
    });
    installTestTransport(dashboardServer(requests, findings));
    const { user, router } = renderDashboard();
    expect(await screen.findByText("Sparse models beat dense ones on long inputs.")).toBeInTheDocument();
    expect(screen.getByText(/Stale/)).toBeInTheDocument();
    expect(screen.getByText(/1 uncited point removed/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "A cited test paper" }));
    await waitFor(() => expect(router.state.location.search).toBe("?paper=7"));
  });

  it("says when AI summaries are off", async () => {
    installTestTransport(dashboardServer([]));
    renderDashboard();
    expect(await screen.findByText(/AI summaries are off on this server/)).toBeInTheDocument();
  });
});
