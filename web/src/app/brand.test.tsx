/**
 * The brand logo (UI.md §2, §4, §6): the R mark and lockup in the app shell and the sign-in header,
 * inside links named "Ratiocination home", themed through currentColor and --accent.
 */
import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { describe, expect, it, vi } from "vitest";
import { BrandLockup, BrandMark } from "../components/ui/BrandMark";
import { makeAuthConfig, makeMe } from "../test/fixtures";
import { createTestQueryClient, errorResponse, installTestTransport, jsonResponse, type TestHandler } from "../test/utils";
import { AppProviders } from "./providers";
import { createRoutes } from "./router";

const { stubPage } = vi.hoisted(() => ({
  stubPage: (title: string) => async () => {
    const { createElement } = await import("react");
    return { default: () => createElement("h1", null, title) };
  },
}));
vi.mock("../pages/ProjectsPage", stubPage("Projects"));
vi.mock("../pages/SignInPage", stubPage("Sign in"));

const localServer: TestHandler = (request) => {
  if (request.path === "/api/me") return jsonResponse(makeMe({ mode: "local" }));
  if (request.path === "/api/auth/config") return jsonResponse(makeAuthConfig({ mode: "local" }));
  if (request.path === "/api/projects") return jsonResponse([]);
  return errorResponse(501, "not_implemented", "not mocked");
};

const signedOutServer: TestHandler = (request) => {
  if (request.path === "/api/auth/config") return jsonResponse(makeAuthConfig());
  return errorResponse(401, "unauthenticated", "Sign in to continue");
};

function renderApp(path: string, handler: TestHandler) {
  installTestTransport(handler);
  const queryClient = createTestQueryClient();
  const router = createMemoryRouter(createRoutes(queryClient), { initialEntries: [path] });
  render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

function brandSvgs(link: HTMLElement): SVGElement[] {
  return Array.from(link.querySelectorAll<SVGElement>("svg[data-brand]"));
}

describe("brand logo in the app", () => {
  it("shows the R mark (rail) and the lockup (full sidebar) in the sidebar's home link, and the mark in the top bar", async () => {
    renderApp("/projects", localServer);
    await screen.findByRole("heading", { level: 1, name: "Projects" }, { timeout: 3000 });

    const sidebar = screen.getByRole("complementary", { name: "Sidebar" });
    const sidebarHome = within(sidebar).getByRole("link", { name: "Ratiocination home" });
    expect(sidebarHome).toHaveAttribute("href", "/");
    expect(brandSvgs(sidebarHome).map((svg) => svg.dataset.brand)).toEqual(["mark", "lockup"]);

    const homeLinks = screen.getAllByRole("link", { name: "Ratiocination home" });
    expect(homeLinks).toHaveLength(2);
    const topBarHome = homeLinks.find((link) => link !== sidebarHome);
    expect(topBarHome && brandSvgs(topBarHome).map((svg) => svg.dataset.brand)).toEqual(["mark"]);

    // Decorative inside the labelled links: the link carries the name.
    for (const link of homeLinks) {
      for (const svg of brandSvgs(link)) expect(svg).toHaveAttribute("aria-hidden", "true");
    }
    expect(screen.queryByRole("img", { name: /radar/i })).toBeNull();
  });

  it("shows the lockup above the sign-in card", async () => {
    renderApp("/signin", signedOutServer);
    await screen.findByRole("heading", { level: 1, name: "Sign in" }, { timeout: 3000 });
    const home = screen.getByRole("link", { name: "Ratiocination home" });
    expect(home).toHaveAttribute("href", "/");
    expect(brandSvgs(home).map((svg) => svg.dataset.brand)).toEqual(["lockup"]);
  });
});

describe("BrandMark and BrandLockup", () => {
  it("are decorative by default and named images with a title", () => {
    const { container } = render(
      <>
        <BrandMark />
        <BrandMark title="Ratiocination" size={40} />
        <BrandLockup title="Ratiocination lockup" height={30} />
      </>,
    );
    const [decorative] = Array.from(container.querySelectorAll("svg"));
    expect(decorative).toHaveAttribute("aria-hidden", "true");
    expect(decorative).toHaveAttribute("width", "28");
    const mark = screen.getByRole("img", { name: "Ratiocination" });
    expect(mark).toHaveAttribute("width", "40");
    expect(mark).toHaveAttribute("height", "40");
    const lockup = screen.getByRole("img", { name: "Ratiocination lockup" });
    expect(lockup).toHaveAttribute("height", "30");
    expect(Number(lockup.getAttribute("width"))).toBeCloseTo(187.69, 1);
  });

  it("paint the R and wordmark in the text colour and the triangle in the theme accent", () => {
    const { container } = render(
      <>
        <BrandMark />
        <BrandLockup />
      </>,
    );
    const fills = (brand: string) => Array.from(container.querySelectorAll(`svg[data-brand="${brand}"] path`)).map((path) => path.getAttribute("fill"));
    expect(fills("mark")).toEqual(["currentColor", "var(--accent)"]);
    expect(fills("lockup")).toEqual(["currentColor", "var(--accent)", "currentColor"]);
  });
});
