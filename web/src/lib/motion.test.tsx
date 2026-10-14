import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { setMatchMedia } from "../test/matchMedia";
import {
  MOTION_STORAGE_KEY,
  MotionPreferenceProvider,
  pageTransition,
  setMotionPreference,
  staggerDelay,
  useMotionPreference,
} from "./motion";

const html = () => document.documentElement;
const REDUCE = "(prefers-reduced-motion: reduce)";

describe("useMotionPreference", () => {
  it("uses the storage key rr.motion", () => {
    expect(MOTION_STORAGE_KEY).toBe("rr.motion");
  });

  it('defaults to "system": full motion when the OS does not ask for less', () => {
    const { result } = renderHook(() => useMotionPreference(), { wrapper: MotionPreferenceProvider });
    expect(result.current).toMatchObject({ preference: "system", reduced: false });
    expect(html().getAttribute("data-motion")).toBe("full");
  });

  it("rr.motion=reduced sets data-motion=reduced", () => {
    localStorage.setItem("rr.motion", "reduced");
    const { result } = renderHook(() => useMotionPreference(), { wrapper: MotionPreferenceProvider });
    expect(result.current).toMatchObject({ preference: "reduced", reduced: true });
    expect(html().getAttribute("data-motion")).toBe("reduced");
  });

  it('"system" follows prefers-reduced-motion, including live changes', () => {
    setMatchMedia({ [REDUCE]: true });
    const { result } = renderHook(() => useMotionPreference(), { wrapper: MotionPreferenceProvider });
    expect(result.current.reduced).toBe(true);
    expect(html().getAttribute("data-motion")).toBe("reduced");

    act(() => setMatchMedia({ [REDUCE]: false }));
    expect(result.current.reduced).toBe(false);
    expect(html().getAttribute("data-motion")).toBe("full");
  });

  it("setPreference persists and overrides the OS setting", () => {
    setMatchMedia({ [REDUCE]: true });
    const { result } = renderHook(() => useMotionPreference(), { wrapper: MotionPreferenceProvider });
    act(() => result.current.setPreference("full"));
    expect(localStorage.getItem("rr.motion")).toBe("full");
    expect(result.current).toMatchObject({ preference: "full", reduced: false });
    expect(html().getAttribute("data-motion")).toBe("full");

    act(() => result.current.setPreference("reduced"));
    expect(localStorage.getItem("rr.motion")).toBe("reduced");
    expect(html().getAttribute("data-motion")).toBe("reduced");
  });

  it("setMotionPreference works outside React", () => {
    setMotionPreference("reduced");
    expect(html().getAttribute("data-motion")).toBe("reduced");
  });
});

describe("motion constants", () => {
  it("caps the list stagger at 10 items and drops it when reduced", () => {
    expect(staggerDelay(0)).toBe(0);
    expect(staggerDelay(3)).toBeCloseTo(0.09);
    expect(staggerDelay(50)).toBeCloseTo(0.27);
    expect(staggerDelay(5, true)).toBe(0);
  });

  it("page transitions rise 8px only with full motion", () => {
    expect(pageTransition(false).initial).toEqual({ opacity: 0, y: 8 });
    expect(pageTransition(true).initial).toEqual({ opacity: 0 });
  });
});
