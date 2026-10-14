/**
 * Vitest setup: jest-dom matchers, Motion animations skipped, jsdom polyfills, the global Cytoscape mock,
 * and per-test resets (DOM, API transport, feedback queues, hotkeys, storage, html attributes).
 * Helpers for tests live in ./utils (render with providers, fake API transport) and ./matchMedia.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { MotionGlobalConfig } from "motion/react";
import { afterEach, vi } from "vitest";
import { setTransport } from "../api/client";
import { resetFeedbackState } from "../api/hooks";
import { resetHotkeys } from "../lib/hotkeys";
import { resetCytoscapeMock } from "./cytoscape";
import { installMatchMedia, resetMatchMedia } from "./matchMedia";

// ---- Motion: animations jump to their end state, so assertions never race an entrance fade ----
MotionGlobalConfig.skipAnimations = true;

// ---- Cytoscape: jsdom has no canvas; every test gets the in-memory fake (see ./cytoscape) ----
vi.mock("cytoscape", async () => (await import("./cytoscape")).createCytoscapeModule());
vi.mock("cytoscape-fcose", () => ({ default: function fcose() {} }));

// ---- polyfills for APIs jsdom does not implement (skipped in `// @vitest-environment node` files) ----
const hasDom = typeof window !== "undefined" && typeof document !== "undefined";

class ResizeObserverPolyfill {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

class IntersectionObserverPolyfill {
  readonly root = null;
  readonly rootMargin = "0px";
  readonly scrollMargin = "0px";
  readonly thresholds: readonly number[] = [0];
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

function installDomPolyfills(): void {
  installMatchMedia();
  if (!("ResizeObserver" in globalThis)) {
    globalThis.ResizeObserver = ResizeObserverPolyfill as unknown as typeof ResizeObserver;
  }
  if (!("IntersectionObserver" in globalThis)) {
    globalThis.IntersectionObserver = IntersectionObserverPolyfill as unknown as typeof IntersectionObserver;
  }

  const elementProto = Element.prototype as unknown as Record<string, unknown>;
  const elementPolyfills: Record<string, () => unknown> = {
    hasPointerCapture: () => false,
    setPointerCapture: () => undefined,
    releasePointerCapture: () => undefined,
    scrollIntoView: () => undefined,
  };
  for (const [name, fn] of Object.entries(elementPolyfills)) {
    if (typeof elementProto[name] !== "function") elementProto[name] = fn;
  }

  // jsdom logs "Not implemented" for window.scrollTo; make it a no-op.
  window.scrollTo = (() => undefined) as typeof window.scrollTo;

  const urlStatics = URL as unknown as Record<string, unknown>;
  if (typeof urlStatics.createObjectURL !== "function") urlStatics.createObjectURL = () => "blob:mock";
  if (typeof urlStatics.revokeObjectURL !== "function") urlStatics.revokeObjectURL = () => undefined;
}

if (hasDom) installDomPolyfills();

// ---- per-test isolation ----
afterEach(() => {
  cleanup();
  setTransport(null);
  resetFeedbackState();
  resetHotkeys();
  resetCytoscapeMock();
  resetMatchMedia();
  if (!hasDom) return;
  try {
    window.localStorage.clear();
    window.sessionStorage.clear();
  } catch {
    // storage unavailable: nothing to clear
  }
  const root = document.documentElement;
  root.removeAttribute("data-theme");
  root.removeAttribute("data-motion");
  root.removeAttribute("style");
});
