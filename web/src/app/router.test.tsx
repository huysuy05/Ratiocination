import { act, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { describe, expect, it, vi } from "vitest";
import { UNAUTHORIZED_EVENT } from "../api/client";
import { makeAuthConfig, makeMe, makeProject } from "../test/fixtures";
import { createTestQueryClient, errorResponse, installTestTransport, jsonResponse, type TestHandler } from "../test/utils";
import { AppProviders } from "./providers";
import { createRoutes } from "./router";

// Routing is tested on its own: every page module is replaced by a heading with its name.
const { stubPage } = vi.hoisted(() => ({
  stubPage: (title: string) => async () => {
    const { createElement } = await import("react");
    return { default: () => createElement("h1", null, title) };
  },
}));
vi.mock("../pages/ProjectsPage", stubPage("Projects"));
vi.mock("../pages/NewProjectPage", stubPage("New project"));
vi.mock("../pages/FeedPage", stubPage("Feed"));
vi.mock("../pages/LibraryPage", stubPage("Library"));
vi.mock("../pages/GraphPage", stubPage("Graph"));
vi.mock("../pages/DocumentsPage", stubPage("Documents"));
vi.mock("../pages/ProjectSettingsPage", stubPage("Project settings"));
vi.mock("../pages/PaperPage", stubPage("Paper"));
vi.mock("../pages/DashboardPage", stubPage("Dashboard"));
vi.mock("../pages/SettingsPage", stubPage("Settings"));
vi.mock("../pages/SignInPage", stubPage("Sign in"));
vi.mock("../pages/SignUpPage", stubPage("Create your account"));
vi.mock("../pages/ForgotPasswordPage", stubPage("Reset your password"));
vi.mock("../pages/ResetPasswordPage", stubPage("Choose a new password"));
vi.mock("../pages/VerifyEmailPage", stubPage("Verify your email"));
vi.mock("../pages/LandingPage", stubPage("Landing"));

const LOCAL_ME = makeMe({ mode: "local" });

/** Signed in (local mode) with no projects; anything else is "not mocked". */
const localServer: TestHandler = (request) => {
  if (request.path === "/api/me") return jsonResponse(LOCAL_ME);
  if (request.path === "/api/auth/config") return jsonResponse(makeAuthConfig({ mode: "local" }));
  if (request.path === "/api/projects") return jsonResponse([]);
  return errorResponse(501, "not_implemented", "not mocked");
};

/** Accounts mode, signed out. */
const signedOutServer: TestHandler = (request) => {
  if (request.path === "/api/auth/config") return jsonResponse(makeAuthConfig());
  return errorResponse(401, "unauthenticated", "Sign in to continue");
};

function renderApp(path: string, handler: TestHandler = localServer) {
  const transport = installTestTransport(handler);
  const queryClient = createTestQueryClient();
  const router = createMemoryRouter(createRoutes(queryClient), { initialEntries: [path] });
  render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return { router, queryClient, ...transport };
}

function findTitle(name: string) {
  return screen.findByRole("heading", { level: 1, name }, { timeout: 3000 });
}

describe("/ redirect", () => {
  it("goes to /projects when no project was opened before", async () => {
    const { router } = renderApp("/");
    await findTitle("Projects");
    expect(router.state.location.pathname).toBe("/projects");
  });

  it("goes to the last project's feed when it still exists", async () => {
    localStorage.setItem("rr.lastProjectId", "5");
    const { router, requests } = renderApp("/", (request) =>
      request.path === "/api/projects/5" ? jsonResponse(makeProject({ id: 5 })) : localServer(request),
    );
    await findTitle("Feed");
    expect(router.state.location.pathname).toBe("/projects/5/feed");
    expect(requests.some((request) => request.method === "GET" && request.path === "/api/projects/5")).toBe(true);
    expect(localStorage.getItem("rr.lastProjectId")).toBe("5");
  });

  it("clears the key and goes to /projects when the project is gone (404)", async () => {
    localStorage.setItem("rr.lastProjectId", "5");
    const { router } = renderApp("/", (request) =>
      request.path === "/api/projects/5" ? errorResponse(404, "not_found", "Project not found") : localServer(request),
    );
    await findTitle("Projects");
    expect(router.state.location.pathname).toBe("/projects");
    expect(localStorage.getItem("rr.lastProjectId")).toBeNull();
  });

  it("keeps the key on a network error but still goes to /projects", async () => {
    localStorage.setItem("rr.lastProjectId", "5");
    const { router } = renderApp("/", (request) => {
      if (request.path === "/api/projects/5") throw new TypeError("Failed to fetch");
      return localServer(request);
    });
    await findTitle("Projects");
    expect(router.state.location.pathname).toBe("/projects");
    expect(localStorage.getItem("rr.lastProjectId")).toBe("5");
  });

  it("drops a malformed key without calling the API", async () => {
    localStorage.setItem("rr.lastProjectId", "abc");
    const { router, requests } = renderApp("/");
    await findTitle("Projects");
    expect(router.state.location.pathname).toBe("/projects");
    expect(localStorage.getItem("rr.lastProjectId")).toBeNull();
    expect(requests.some((request) => request.path.startsWith("/api/projects/"))).toBe(false);
  });
});

describe("landing page", () => {
  /** Accounts mode, signed in, no projects. */
  const signedInServer: TestHandler = (request) => (request.path === "/api/me" ? jsonResponse(makeMe()) : localServer(request));

  it("shows the landing page to a signed-out visitor at / (no redirect to sign in)", async () => {
    const { router, requests } = renderApp("/", signedOutServer);
    await findTitle("Landing");
    expect(router.state.location.pathname).toBe("/");
    expect(requests.filter((request) => request.path === "/api/me")).toHaveLength(1);
  });

  it("keeps the last project key for a signed-out visitor", async () => {
    localStorage.setItem("rr.lastProjectId", "5");
    const { requests } = renderApp("/", signedOutServer);
    await findTitle("Landing");
    expect(localStorage.getItem("rr.lastProjectId")).toBe("5");
    expect(requests.some((request) => request.path.startsWith("/api/projects"))).toBe(false);
  });

  it("redirects a signed-in visitor at / into the app", async () => {
    const { router, requests } = renderApp("/", signedInServer);
    await findTitle("Projects");
    expect(router.state.location.pathname).toBe("/projects");
    // The loader's answer is reused by the auth gate.
    expect(requests.filter((request) => request.path === "/api/me")).toHaveLength(1);
  });

  it("redirects a signed-in visitor at / to the last project's feed", async () => {
    localStorage.setItem("rr.lastProjectId", "5");
    const { router } = renderApp("/", (request) =>
      request.path === "/api/projects/5" ? jsonResponse(makeProject({ id: 5 })) : signedInServer(request),
    );
    await findTitle("Feed");
    expect(router.state.location.pathname).toBe("/projects/5/feed");
  });

  it.each([
    ["signed out", signedOutServer],
    ["signed in", signedInServer],
    ["in local mode", localServer],
  ])("renders /welcome %s, without checking the session", async (_label, handler) => {
    const { router, requests } = renderApp("/welcome", handler);
    await findTitle("Landing");
    expect(router.state.location.pathname).toBe("/welcome");
    expect(requests.filter((request) => request.path === "/api/me")).toHaveLength(0);
  });

  it.each(["/projects", "/projects/3/feed", "/dashboard?days=7", "/settings", "/nope"])(
    "still sends a signed-out visitor from %s to sign in",
    async (path) => {
      const { router } = renderApp(path, signedOutServer);
      await findTitle("Sign in");
      expect(router.state.location.pathname).toBe("/signin");
      expect(router.state.location.search).toBe(`?next=${encodeURIComponent(path)}`);
    },
  );

  it("takes the app's retry path when the session check fails at /", { timeout: 10_000 }, async () => {
    renderApp("/", (request) => {
      if (request.path === "/api/me") throw new TypeError("Failed to fetch");
      return localServer(request);
    });
    expect(await screen.findByText("Ratiocination is not reachable", {}, { timeout: 6000 })).toBeVisible();
  });
});

describe("route table", () => {
  it("redirects /projects/:projectId to its feed, keeping the query string", async () => {
    const { router } = renderApp("/projects/7?paper=3");
    await findTitle("Feed");
    expect(router.state.location.pathname).toBe("/projects/7/feed");
    expect(router.state.location.search).toBe("?paper=3");
  });

  it.each([
    ["/projects", "Projects"],
    ["/projects/new", "New project"],
    ["/projects/3/feed", "Feed"],
    ["/projects/3/library", "Library"],
    ["/projects/3/graph", "Graph"],
    ["/projects/3/graph/42", "Graph"],
    ["/projects/3/documents", "Documents"],
    ["/projects/3/settings", "Project settings"],
    ["/papers/9?project=3", "Paper"],
    ["/dashboard?days=7", "Dashboard"],
    ["/settings", "Settings"],
  ])("%s renders %s", async (path, title) => {
    renderApp(path);
    await findTitle(title);
  });

  it.each(["/nope", "/projects/3/unknown", "/papers", "/projects/3/calibration", "/costs"])(
    "%s renders NotFound",
    async (path) => {
      renderApp(path);
      await findTitle("Page not found");
      expect(screen.getByRole("link", { name: "Go to your projects" })).toHaveAttribute("href", "/projects");
    },
  );
});

describe("auth gate", () => {
  it("sends a signed-out visitor to sign in, remembering where they were going", async () => {
    const { router } = renderApp("/projects/3/library?paper=4", signedOutServer);
    await findTitle("Sign in");
    expect(router.state.location.pathname).toBe("/signin");
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent("/projects/3/library?paper=4")}`);
  });

  it("checks the session once when signed out (no refetch loop on its own 401)", async () => {
    const { requests } = renderApp("/projects", signedOutServer);
    await findTitle("Sign in");
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(requests.filter((request) => request.path === "/api/me")).toHaveLength(1);
  });

  it.each([
    ["/signin", "Sign in"],
    ["/signup", "Create your account"],
    ["/forgot", "Reset your password"],
    ["/reset?token=t", "Choose a new password"],
    ["/verify?token=t", "Verify your email"],
  ])("%s renders %s in accounts mode", async (path, title) => {
    renderApp(path, signedOutServer);
    await findTitle(title);
  });

  it("redirects the auth pages home in local mode", async () => {
    const { router } = renderApp("/signin");
    await findTitle("Projects");
    expect(router.state.location.pathname).toBe("/projects");
  });

  it("re-checks the session on radar:unauthorized and then asks to sign in", async () => {
    let signedIn = true;
    const { router } = renderApp("/projects", (request) => {
      if (request.path === "/api/me") {
        return signedIn ? jsonResponse(makeMe()) : errorResponse(401, "unauthenticated", "Sign in to continue");
      }
      return signedIn ? localServer(request) : signedOutServer(request);
    });
    await findTitle("Projects");
    signedIn = false;
    act(() => {
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: { path: "/api/projects", request_id: "r" } }));
    });
    await findTitle("Sign in");
    expect(router.state.location.pathname).toBe("/signin");
  });

  it("shows a retry screen when the server cannot be reached", { timeout: 10_000 }, async () => {
    renderApp("/projects", (request) => {
      if (request.path === "/api/me") throw new TypeError("Failed to fetch");
      return localServer(request);
    });
    // useMe retries network errors twice (1 s + 2 s backoff) before giving up.
    expect(await screen.findByText("Ratiocination is not reachable", {}, { timeout: 6000 })).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
  });
});

describe("AppShell", () => {
  it("remembers the project of /projects/:projectId/* routes in rr.lastProjectId", async () => {
    renderApp("/projects/9/library");
    await findTitle("Library");
    expect(localStorage.getItem("rr.lastProjectId")).toBe("9");
  });

  it("does not treat /projects/new as a project", async () => {
    localStorage.setItem("rr.lastProjectId", "4");
    renderApp("/projects/new");
    await findTitle("New project");
    expect(localStorage.getItem("rr.lastProjectId")).toBe("4");
  });

  it("shows project and workspace navigation", async () => {
    renderApp("/projects/9/feed");
    await findTitle("Feed");
    const projectNav = screen.getByRole("navigation", { name: "Project" });
    expect(projectNav.querySelector('a[href="/projects/9/library"]')).not.toBeNull();
    expect(projectNav.querySelector('a[href="/projects/9/graph"]')).not.toBeNull();
    const workspace = screen.getByRole("navigation", { name: "Workspace" });
    expect(workspace.querySelector('a[href="/dashboard"]')).not.toBeNull();
    expect(workspace.querySelector('a[href="/settings"]')).not.toBeNull();
  });

  it("shows the account menu only with accounts", async () => {
    renderApp("/projects", (request) => (request.path === "/api/me" ? jsonResponse(makeMe()) : localServer(request)));
    await findTitle("Projects");
    expect((await screen.findAllByRole("button", { name: "Account: Test User" })).length).toBeGreaterThan(0);
  });

  it("has no account menu in local mode", async () => {
    renderApp("/projects");
    await findTitle("Projects");
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Workspace" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /^Account:/ })).toBeNull();
  });
});
