import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LandingPage from "../../pages/LandingPage";
import { makeMe } from "../../test/fixtures";
import { setMatchMedia } from "../../test/matchMedia";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders } from "../../test/utils";

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});

function renderLanding() {
  return renderWithProviders(<LandingPage />, { route: "/welcome" });
}

describe("LandingPage", () => {
  /*
   * The app shell's logo points at /welcome, so a signed-in reader lands here. Telling them to
   * "Sign in" (as this page did until 2026-10-15) is wrong and leaves them no way back into the app.
   */
  describe("when the visitor is already signed in", () => {
    beforeEach(() => {
      installTestTransport((request) =>
        request.path === "/api/me" ? jsonResponse(makeMe()) : errorResponse(501, "not_implemented", "not mocked"),
      );
    });

    it("offers the app instead of sign in", async () => {
      renderLanding();
      const nav = screen.getByRole("navigation", { name: "Main" });
      const openApp = await within(nav).findByRole("link", { name: /Open the app as Test User/i });
      expect(openApp).toHaveAttribute("href", "/");
      expect(within(nav).queryByRole("link", { name: "Sign in" })).toBeNull();
      expect(await screen.findByRole("link", { name: "Open my deck" })).toHaveAttribute("href", "/");
      expect(screen.queryByRole("link", { name: "Create an account" })).toBeNull();
    });
  });

  it("has one h1 (the logo) and landmarks", () => {
    renderLanding();
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveAccessibleName(/^Ratiocination/);
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    for (const name of ["We’re Ratiocination, and we’re here to help you find the papers that matter.", "How it works", "arXiv categories"]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    for (const name of [/Swipe a\s*daily deck/i, /Know why\s*every paper/i, /Map the\s*breakthroughs/i]) {
      expect(screen.getByRole("heading", { level: 3, name })).toBeInTheDocument();
    }
  });

  it("links to sign in, sign up and its own sections", () => {
    renderLanding();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/signin");
    expect(within(nav).getByRole("link", { name: "How it works" })).toHaveAttribute("href", "#how-it-works");
    expect(within(nav).getByRole("link", { name: "Graph" })).toHaveAttribute("href", "#feature-graph");
    expect(within(nav).getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "#feature-why");
    expect(screen.getByRole("link", { name: "Create an account" })).toHaveAttribute("href", "/signup");
    expect(screen.getAllByRole("link", { name: "Sign in" }).map((link) => link.getAttribute("href"))).toEqual(["/signin", "/signin"]);
    for (const id of ["how-it-works", "feature-deck", "feature-why", "feature-graph"]) {
      expect(document.getElementById(id)).not.toBeNull();
    }
    expect(screen.getByRole("link", { name: "Source code (AGPL)" })).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("keeps the demo deck and mocks away from assistive tech", () => {
    renderLanding();
    const title = screen.getByText("Sparse routers learn faster when they forget: a study of expert collapse");
    expect(title.closest('[aria-hidden="true"]')).not.toBeNull();
    expect(screen.getByText("Why this paper").closest('[aria-hidden="true"]')).not.toBeNull();
    expect(screen.getByText("CD +0.42 · Disruptive").closest('[aria-hidden="true"]')).not.toBeNull();
    // The sticker is real content.
    expect(screen.getByText("New: a fresh deck every day. Beta.")).toBeInTheDocument();
  });

  it("plays the portrait loop muted, webm first, with the poster shown while it loads", () => {
    const { container } = renderLanding();
    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    expect(video).toHaveAttribute("poster", "/landing/ratiocination-loop-poster.jpg");
    expect(video).toHaveAttribute("preload", "metadata");
    expect(video?.autoplay).toBe(true);
    expect(video?.muted).toBe(true);
    expect(video?.loop).toBe(true);
    expect(video?.closest('[aria-hidden="true"]')).not.toBeNull();
    expect([...(video?.querySelectorAll("source") ?? [])].map((source) => source.getAttribute("type"))).toEqual(["video/webm", "video/mp4"]);
  });

  it("shows the still and no motion controls under reduced motion", () => {
    setMatchMedia({ "(prefers-reduced-motion: reduce)": true });
    const { container } = renderLanding();
    expect(container.querySelector("video")).toBeNull();
    expect(container.querySelector('img[src="/landing/ratiocination-loop-poster.jpg"]')).not.toBeNull();
    expect(screen.queryByRole("button", { name: /moving categories/ })).toBeNull();
  });

  it("lets visitors pause the category marquee", async () => {
    const { user } = renderLanding();
    await user.click(screen.getByRole("button", { name: "Pause the moving categories" }));
    expect(screen.getByRole("button", { name: "Play the moving categories" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "arXiv categories" }).closest("section")).toHaveAttribute("data-paused", "true");
  });

  it("lists each category once for screen readers", () => {
    renderLanding();
    const list = screen.getByRole("heading", { name: "arXiv categories" }).closest("section")!.querySelector("ul")!;
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
  });

  it("picks the logo for the theme and paints the page background while mounted", () => {
    localStorage.setItem("rr.theme", "light");
    const { unmount } = renderLanding();
    expect(screen.getByRole("img", { name: "Ratiocination" })).toHaveAttribute("src", "/landing/ratiocination-logo.png");
    expect(document.documentElement).toHaveClass("lp-page");
    unmount();
    expect(document.documentElement).not.toHaveClass("lp-page");
  });

  it("uses the white logo in the dark theme", () => {
    localStorage.setItem("rr.theme", "dark");
    renderLanding();
    expect(screen.getByRole("img", { name: "Ratiocination" })).toHaveAttribute("src", "/landing/ratiocination-logo-white.png");
  });
});
