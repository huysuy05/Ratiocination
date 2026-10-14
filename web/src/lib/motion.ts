/**
 * Motion preference (UI.md §3, FROZEN): `useMotionPreference()` → `{ preference, setPreference,
 * reduced }`, stored in localStorage `rr.motion` (default "system" = follow the OS), applied as
 * `<html data-motion="reduced|full">`. main.tsx feeds `reduced` into Motion's
 * `<MotionConfig reducedMotion>`; globals.css disables CSS animations under data-motion=reduced;
 * Cytoscape layouts use `animate: !reduced`.
 */
import { createElement, Fragment, useLayoutEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import type { Transition } from "motion/react";
import { readLocal, STORAGE_KEYS, writeLocal } from "./storage";

export type MotionPreference = "system" | "reduced" | "full";

export const MOTION_STORAGE_KEY = STORAGE_KEYS.motion;
export const DEFAULT_MOTION: MotionPreference = "system";
const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

export interface MotionPreferenceState {
  preference: MotionPreference;
  setPreference: (preference: MotionPreference) => void;
  /** True when animations should be reduced (explicit choice, or "system" + OS setting). */
  reduced: boolean;
}

export function isMotionPreference(value: unknown): value is MotionPreference {
  return value === "system" || value === "reduced" || value === "full";
}

export function readStoredMotion(): MotionPreference {
  const stored = readLocal(MOTION_STORAGE_KEY);
  return isMotionPreference(stored) ? stored : DEFAULT_MOTION;
}

function mediaQuery(query: string): MediaQueryList | null {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query) : null;
  } catch {
    return null;
  }
}

export function systemPrefersReducedMotion(): boolean {
  return mediaQuery(REDUCED_QUERY)?.matches ?? false;
}

export function resolveReducedMotion(
  preference: MotionPreference,
  systemReduced: boolean = systemPrefersReducedMotion(),
): boolean {
  if (preference === "system") return systemReduced;
  return preference === "reduced";
}

/** Set `<html data-motion="reduced|full">`. */
export function applyMotion(reduced: boolean): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-motion", reduced ? "reduced" : "full");
}

// ---- external store (shared by every useMotionPreference() caller) ----

interface MotionSnapshot {
  preference: MotionPreference;
  reduced: boolean;
}

const listeners = new Set<() => void>();
let preference: MotionPreference = DEFAULT_MOTION;
let systemReduced = false;
let snapshot: MotionSnapshot = { preference: DEFAULT_MOTION, reduced: false };
let detach: (() => void) | null = null;

function recompute(): boolean {
  const reduced = resolveReducedMotion(preference, systemReduced);
  if (snapshot.preference === preference && snapshot.reduced === reduced) return false;
  snapshot = { preference, reduced };
  return true;
}

function syncFromEnvironment(): boolean {
  preference = readStoredMotion();
  systemReduced = systemPrefersReducedMotion();
  return recompute();
}

function emit(): void {
  for (const listener of [...listeners]) listener();
}

function attach(): () => void {
  const query = mediaQuery(REDUCED_QUERY);
  const onMediaChange = (event: MediaQueryListEvent) => {
    systemReduced = event.matches;
    if (recompute()) {
      applyMotion(snapshot.reduced);
      emit();
    }
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== MOTION_STORAGE_KEY) return;
    if (syncFromEnvironment()) {
      applyMotion(snapshot.reduced);
      emit();
    }
  };
  query?.addEventListener?.("change", onMediaChange);
  window.addEventListener("storage", onStorage);
  return () => {
    query?.removeEventListener?.("change", onMediaChange);
    window.removeEventListener("storage", onStorage);
  };
}

function subscribe(listener: () => void): () => void {
  if (listeners.size === 0) {
    syncFromEnvironment();
    if (typeof window !== "undefined") detach = attach();
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      detach?.();
      detach = null;
    }
  };
}

function getSnapshot(): MotionSnapshot {
  if (listeners.size === 0) syncFromEnvironment();
  return snapshot;
}

function getServerSnapshot(): MotionSnapshot {
  return snapshot;
}

/** Persist and apply a motion preference (works outside React too). */
export function setMotionPreference(next: MotionPreference): void {
  preference = next;
  writeLocal(MOTION_STORAGE_KEY, next);
  recompute();
  applyMotion(snapshot.reduced);
  emit();
}

export function useMotionPreference(): MotionPreferenceState {
  const current = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return useMemo(
    () => ({ preference: current.preference, reduced: current.reduced, setPreference: setMotionPreference }),
    [current],
  );
}

/** Keeps `<html data-motion>` in sync with the preference. Mount once near the root. */
export function MotionPreferenceProvider({ children }: { children?: ReactNode }) {
  const { reduced } = useMotionPreference();
  useLayoutEffect(() => {
    applyMotion(reduced);
  }, [reduced]);
  return createElement(Fragment, null, children);
}

// ---- shared animation constants (UI.md §3) ----

/** Default spring. */
export const SPRING = { type: "spring", stiffness: 420, damping: 34 } as const satisfies Transition;
/** Gentle spring (larger surfaces, sheets). */
export const SPRING_GENTLE = { type: "spring", stiffness: 260, damping: 30 } as const satisfies Transition;
/** UI fades: 150–220 ms. */
export const FADE_FAST = { duration: 0.15, ease: "easeOut" } as const satisfies Transition;
export const FADE = { duration: 0.2, ease: "easeOut" } as const satisfies Transition;
export const FADE_SLOW = { duration: 0.22, ease: "easeOut" } as const satisfies Transition;
/** The radar sweep: one turn per 2.4 s, only while a job is running. */
export const RADAR_SWEEP_SECONDS = 2.4;
/** List stagger: 30 ms per item, at most 10 items. */
export const STAGGER_SECONDS = 0.03;
export const STAGGER_MAX_ITEMS = 10;

/** Stagger delay (seconds) for the item at `index` (capped at 10 items; 0 when reduced). */
export function staggerDelay(index: number, reduced = false): number {
  if (reduced) return 0;
  return Math.min(Math.max(index, 0), STAGGER_MAX_ITEMS - 1) * STAGGER_SECONDS;
}

/** Page transition: fade + 8 px rise (fade only when reduced). Spread onto a motion element. */
export function pageTransition(reduced: boolean) {
  return {
    initial: reduced ? { opacity: 0 } : { opacity: 0, y: 8 },
    animate: reduced ? { opacity: 1 } : { opacity: 1, y: 0 },
    exit: { opacity: 0 },
    transition: FADE,
  } as const;
}
