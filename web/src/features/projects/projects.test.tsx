import { screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import type { Project, ProjectUpdate } from "../../api/types";
import NewProjectPage from "../../pages/NewProjectPage";
import ProjectSettingsPage from "../../pages/ProjectSettingsPage";
import ProjectsPage from "../../pages/ProjectsPage";
import { makeProject } from "../../test/fixtures";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders, type TestHandler } from "../../test/utils";
import { categoryLabel, matchesCategory } from "./categories";

function routes(element: ReactElement, path: string) {
  return [
    { path, element },
    { path: "/projects/:projectId/feed", element: <h1>Feed page</h1> },
    { path: "/projects", element: <ProjectsPage /> },
  ];
}

describe("categories", () => {
  it("labels and filters categories", () => {
    expect(categoryLabel("cs.IR")).toBe("Information Retrieval (cs.IR)");
    expect(categoryLabel("xx.YY")).toBe("xx.YY");
    expect(matchesCategory({ code: "cs.IR", name: "Information Retrieval" }, "retri")).toBe(true);
    expect(matchesCategory({ code: "cs.IR", name: "Information Retrieval" }, "CS.ir")).toBe(true);
    expect(matchesCategory({ code: "cs.IR", name: "Information Retrieval" }, "vision")).toBe(false);
  });
});

describe("ProjectsPage", () => {
  it("shows the first-project empty state", async () => {
    installTestTransport(() => jsonResponse([]));
    renderWithProviders(<ProjectsPage />);
    expect(await screen.findByRole("heading", { name: "Create your first project" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "New project" })[0]).toHaveAttribute("href", "/projects/new");
  });

  it("lists projects with their deck state; drafts link to settings", async () => {
    installTestTransport(() =>
      jsonResponse([
        makeProject({ id: 1, name: "Live radar", stats: { ...makeProject().stats, remaining_in_deck: 7, relevant: 4 } }),
        makeProject({ id: 2, name: "Draft radar", active: false }),
      ]),
    );
    renderWithProviders(<ProjectsPage />);
    const live = await screen.findByRole("link", { name: /Live radar/ });
    expect(live).toHaveAttribute("href", "/projects/1/feed");
    expect(within(live).getByText("7 new")).toBeInTheDocument();
    const draft = screen.getByRole("link", { name: /Draft radar/ });
    expect(draft).toHaveAttribute("href", "/projects/2/settings");
    expect(within(draft).getByText("Draft")).toBeInTheDocument();
  });
});

describe("NewProjectPage", () => {
  function wizardServer(log: { creates: unknown[]; patches: ProjectUpdate[] }): TestHandler {
    let project: Project | null = null;
    return (request) => {
      if (request.path === "/api/projects" && request.method === "POST") {
        log.creates.push(request.body);
        const body = request.body as { name: string; research_question: string; keywords: string[]; settings: Project["settings"] };
        project = makeProject({ id: 42, name: body.name, research_question: body.research_question, keywords: body.keywords, active: false, settings: body.settings });
        return jsonResponse(project, 201);
      }
      if (request.path === "/api/projects/42" && request.method === "PATCH") {
        const patch = request.body as ProjectUpdate;
        log.patches.push(patch);
        project = { ...(project ?? makeProject({ id: 42, active: false })), ...(patch.active !== undefined ? { active: patch.active } : {}) };
        return jsonResponse(project);
      }
      if (request.path === "/api/projects/42") return jsonResponse(project ?? makeProject({ id: 42, active: false, name: "Resumed draft" }));
      if (request.path.endsWith("/seeds") || request.path.endsWith("/documents")) return jsonResponse([]);
      return errorResponse(404, "not_found", "not mocked");
    };
  }

  it("requires a name and a question before moving on", async () => {
    installTestTransport(() => jsonResponse([]));
    const { user } = renderWithProviders(null, { route: "/projects/new", routes: routes(<NewProjectPage />, "/projects/new") });
    await user.click(await screen.findByRole("button", { name: /Next/ }));
    expect(screen.getByText("Give the project a name.")).toBeInTheDocument();
    expect(screen.getByText("Write your research question.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What are you working on?" })).toBeInTheDocument();
  });

  it("creates a draft with the chosen sources, then launches it and opens the feed", async () => {
    const log = { creates: [] as unknown[], patches: [] as ProjectUpdate[] };
    installTestTransport(wizardServer(log));
    const { user, router } = renderWithProviders(null, { route: "/projects/new", routes: routes(<NewProjectPage />, "/projects/new") });

    await user.type(await screen.findByLabelText(/Project name/), "Sparse retrieval");
    await user.type(screen.getByLabelText(/Research question/), "How do sparse models scale to long documents?");
    await user.type(screen.getByRole("textbox", { name: "Keywords" }), "sparse retrieval{Enter}long documents,");
    expect(within(screen.getByRole("list", { name: "Keywords added" })).getAllByRole("listitem")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: /Next/ }));

    await screen.findByRole("heading", { name: "Where should it look?" });
    await user.click(screen.getByRole("checkbox", { name: /Information Retrieval/ }));
    await user.type(screen.getByRole("textbox", { name: "Search arXiv categories" }), "cs.XX{Enter}");
    await user.click(screen.getByRole("button", { name: /Create draft/ }));

    await screen.findByRole("heading", { name: "Papers you already trust" });
    expect(log.creates).toEqual([
      {
        name: "Sparse retrieval",
        research_question: "How do sparse models scale to long documents?",
        description: "",
        keywords: ["sparse retrieval", "long documents"],
        settings: { arxiv_categories: ["cs.IR", "cs.XX"], time_window_days: 14, slate_size: 20 },
        active: false,
      },
    ]);
    expect(router.state.location.search).toBe("?project=42&step=2");

    await user.click(screen.getByRole("button", { name: /Skip for now/ }));
    await screen.findByRole("heading", { name: "Your draft" });
    await user.click(screen.getByRole("button", { name: /Skip for now/ }));
    await screen.findByRole("heading", { name: "Ready to launch" });
    expect(screen.getByText("Information Retrieval (cs.IR)")).toBeInTheDocument();
    expect(screen.getByText("sparse retrieval")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Launch my project/ }));
    await screen.findByRole("heading", { name: "Feed page" });
    expect(log.patches).toEqual([{ active: true }]);
    expect(router.state.location.pathname).toBe("/projects/42/feed");
  }, 15_000); // the whole five-step flow with typing

  it("points back to the Basics step when the server refuses a keyword", async () => {
    installTestTransport((request) => {
      if (request.path === "/api/projects" && request.method === "POST") {
        return errorResponse(422, "validation_error", "Invalid request", { fields: [{ path: "keywords.0", message: "String should have at most 60 characters" }] });
      }
      return jsonResponse([]);
    });
    const { user } = renderWithProviders(null, { route: "/projects/new", routes: routes(<NewProjectPage />, "/projects/new") });
    await user.type(await screen.findByLabelText(/Project name/), "Keywords");
    await user.type(screen.getByLabelText(/Research question/), "Test question?");
    await user.type(screen.getByRole("textbox", { name: "Keywords" }), "RAG{Enter}");
    await user.click(screen.getByRole("button", { name: /Next/ }));
    await user.click(await screen.findByRole("button", { name: /Create draft/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check the Basics step: String should have at most 60 characters");
  });

  it("resumes a draft from the URL", async () => {
    const log = { creates: [] as unknown[], patches: [] as ProjectUpdate[] };
    installTestTransport(wizardServer(log));
    renderWithProviders(null, { route: "/projects/new?project=42&step=4", routes: routes(<NewProjectPage />, "/projects/new") });
    expect(await screen.findByRole("heading", { name: "Ready to launch" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Test research question?")).toBeInTheDocument());
  });
});

describe("ProjectSettingsPage", () => {
  it("saves only the edited section", async () => {
    const patches: unknown[] = [];
    let project = makeProject({ id: 5, name: "My radar" });
    installTestTransport((request) => {
      if (request.path === "/api/projects/5" && request.method === "PATCH") {
        patches.push(request.body);
        project = { ...project, ...(request.body as Partial<Project>), updated_at: "2026-10-12T09:00:00Z" };
        return jsonResponse(project);
      }
      if (request.path === "/api/projects/5") return jsonResponse(project);
      if (request.path.endsWith("/seeds")) return jsonResponse([]);
      return errorResponse(404, "not_found", "not mocked");
    });
    const { user } = renderWithProviders(null, {
      route: "/projects/5/settings",
      routes: routes(<ProjectSettingsPage />, "/projects/:projectId/settings"),
    });
    const name = await screen.findByLabelText(/Project name/);
    expect(screen.getByRole("button", { name: "Save basics" })).toBeDisabled();
    await user.clear(name);
    await user.type(name, "Renamed radar");
    await user.click(screen.getByRole("button", { name: "Save basics" }));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({ name: "Renamed radar", research_question: "Test research question?", description: "", keywords: [] });
  });

  it("edits the keywords next to the question and saves them", async () => {
    const patches: unknown[] = [];
    let project = makeProject({ id: 5, name: "My radar", keywords: ["LLM", "conformal prediction"] });
    installTestTransport((request) => {
      if (request.path === "/api/projects/5" && request.method === "PATCH") {
        patches.push(request.body);
        project = { ...project, ...(request.body as Partial<Project>), updated_at: "2026-10-12T09:00:00Z" };
        return jsonResponse(project);
      }
      if (request.path === "/api/projects/5") return jsonResponse(project);
      if (request.path.endsWith("/seeds")) return jsonResponse([]);
      return errorResponse(404, "not_found", "not mocked");
    });
    const { user } = renderWithProviders(null, {
      route: "/projects/5/settings",
      routes: routes(<ProjectSettingsPage />, "/projects/:projectId/settings"),
    });
    const input = await screen.findByRole("textbox", { name: "Keywords" });
    const save = screen.getByRole("button", { name: "Save basics" });
    expect(within(screen.getByRole("list", { name: "Keywords added" })).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "LLM",
      "conformal prediction",
    ]);
    expect(save).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Remove keyword “LLM”" }));
    await user.type(input, "RAG{Enter}");
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({
      name: "My radar",
      research_question: "Test research question?",
      description: "",
      keywords: ["conformal prediction", "RAG"],
    });
    await waitFor(() => expect(save).toBeDisabled());
  });

  it("shows the server's keyword error under the field", async () => {
    installTestTransport((request) => {
      if (request.path === "/api/projects/5" && request.method === "PATCH") {
        return errorResponse(422, "validation_error", "Invalid request", { fields: [{ path: "keywords.0", message: "String should have at most 60 characters" }] });
      }
      if (request.path === "/api/projects/5") return jsonResponse(makeProject({ id: 5 }));
      if (request.path.endsWith("/seeds")) return jsonResponse([]);
      return errorResponse(404, "not_found", "not mocked");
    });
    const { user } = renderWithProviders(null, {
      route: "/projects/5/settings",
      routes: routes(<ProjectSettingsPage />, "/projects/:projectId/settings"),
    });
    await user.type(await screen.findByRole("textbox", { name: "Keywords" }), "RAG{Enter}");
    await user.click(screen.getByRole("button", { name: "Save basics" }));
    expect(await screen.findByText("String should have at most 60 characters")).toBeInTheDocument();
  });

  it("deletes the project after confirmation", async () => {
    const deleted: string[] = [];
    installTestTransport((request) => {
      if (request.method === "DELETE") {
        deleted.push(request.path);
        return new Response(null, { status: 204 });
      }
      if (request.path === "/api/projects/5") return jsonResponse(makeProject({ id: 5, name: "Doomed" }));
      if (request.path === "/api/projects") return jsonResponse([]);
      if (request.path.endsWith("/seeds")) return jsonResponse([]);
      return errorResponse(404, "not_found", "not mocked");
    });
    const { user, router } = renderWithProviders(null, {
      route: "/projects/5/settings",
      routes: routes(<ProjectSettingsPage />, "/projects/:projectId/settings"),
    });
    await user.click(await screen.findByRole("button", { name: "Delete project" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete project" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects"));
    await waitFor(() => expect(deleted).toEqual(["/api/projects/5"]));
  });
});
