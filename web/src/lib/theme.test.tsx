import { act, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { setMatchMedia } from "../test/matchMedia";
import { resolveTheme, setTheme, ThemeProvider, THEME_STORAGE_KEY, useTheme } from "./theme";

const html = () => document.documentElement;

function ThemeReadout() {
  const { theme, resolved } = useTheme();
  return <output aria-label="theme">{`${theme}/${resolved}`}</output>;
}

describe("useTheme", () => {
  it("uses the storage key rr.theme", () => {
    expect(THEME_STORAGE_KEY).toBe("rr.theme");
  });

  it("defaults to dark and applies <html data-theme> through the provider", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe("dark");
    expect(result.current.resolved).toBe("dark");
    expect(html().getAttribute("data-theme")).toBe("dark");
  });

  it("reads the stored preference", () => {
    localStorage.setItem("rr.theme", "light");
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe("light");
    expect(html().getAttribute("data-theme")).toBe("light");
  });

  it("falls back to dark for an invalid stored value", () => {
    localStorage.setItem("rr.theme", "neon");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
  });

  it("setTheme persists the preference and updates the attribute and every consumer", () => {
    render(
      <ThemeProvider>
        <ThemeReadout />
      </ThemeProvider>,
    );
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme("light"));
    expect(localStorage.getItem("rr.theme")).toBe("light");
    expect(html().getAttribute("data-theme")).toBe("light");
    expect(screen.getByRole("status", { name: "theme" })).toHaveTextContent("light/light");
  });

  it('"system" follows prefers-color-scheme and reacts to OS changes', () => {
    localStorage.setItem("rr.theme", "system");
    setMatchMedia({ "(prefers-color-scheme: light)": true });
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current).toMatchObject({ theme: "system", resolved: "light" });
    expect(html().getAttribute("data-theme")).toBe("light");

    act(() => setMatchMedia({ "(prefers-color-scheme: light)": false }));
    expect(result.current.resolved).toBe("dark");
    expect(html().getAttribute("data-theme")).toBe("dark");
  });

  it("explicit themes ignore the OS preference", () => {
    setMatchMedia({ "(prefers-color-scheme: light)": true });
    expect(resolveTheme("dark")).toBe("dark");
    expect(resolveTheme("system")).toBe("light");
  });

  it("survives storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe("dark");
    expect(() => act(() => result.current.setTheme("light"))).not.toThrow();
    expect(html().getAttribute("data-theme")).toBe("light");
  });

  it("setTheme works outside React", () => {
    setTheme("light");
    expect(html().getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem("rr.theme")).toBe("light");
  });
});
