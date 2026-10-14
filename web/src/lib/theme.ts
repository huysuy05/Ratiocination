/**
 * Theme preference (UI.md §3, FROZEN): `useTheme()` → `{ theme, resolved, setTheme }`, stored in
 * localStorage `rr.theme` (default "dark"), applied as `<html data-theme="dark|light">`. The inline
 * script in index.html applies the stored theme before first paint; `ThemeProvider` keeps the
 * attribute in sync afterwards (including OS changes while the preference is "system").
 */
import { createElement, Fragment, useLayoutEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { readLocal, STORAGE_KEYS, writeLocal } from "./storage";

export type ThemePreference = "system" | "dark" | "light";
export type ResolvedTheme = "dark" | "light";

export const THEME_STORAGE_KEY = STORAGE_KEYS.theme;
export const DEFAULT_THEME: ThemePreference = "dark";
const LIGHT_QUERY = "(prefers-color-scheme: light)";

export interface ThemeState {
  theme: ThemePreference;
  resolved: ResolvedTheme;
  setTheme: (theme: ThemePreference) => void;
}

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "system" || value === "dark" || value === "light";
}

/** The stored preference, or the default ("dark") when missing, invalid or unreadable. */
export function readStoredTheme(): ThemePreference {
  const stored = readLocal(THEME_STORAGE_KEY);
  return isThemePreference(stored) ? stored : DEFAULT_THEME;
}

function mediaQuery(query: string): MediaQueryList | null {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query) : null;
  } catch {
    return null;
  }
}

export function systemPrefersLight(): boolean {
  return mediaQuery(LIGHT_QUERY)?.matches ?? false;
}

export function resolveTheme(theme: ThemePreference, prefersLight: boolean = systemPrefersLight()): ResolvedTheme {
  if (theme === "system") return prefersLight ? "light" : "dark";
  return theme;
}

/** Set `<html data-theme>` (and color-scheme for native controls). */
export function applyTheme(resolved: ResolvedTheme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute("data-theme", resolved);
  root.style.colorScheme = resolved;
}

// ---- external store (shared by every useTheme() caller) ----

interface ThemeSnapshot {
  theme: ThemePreference;
  resolved: ResolvedTheme;
}

const listeners = new Set<() => void>();
let preference: ThemePreference = DEFAULT_THEME;
let prefersLight = false;
let snapshot: ThemeSnapshot = { theme: DEFAULT_THEME, resolved: "dark" };
let detach: (() => void) | null = null;

function recompute(): boolean {
  const resolved = resolveTheme(preference, prefersLight);
  if (snapshot.theme === preference && snapshot.resolved === resolved) return false;
  snapshot = { theme: preference, resolved };
  return true;
}

function syncFromEnvironment(): boolean {
  preference = readStoredTheme();
  prefersLight = systemPrefersLight();
  return recompute();
}

function emit(): void {
  for (const listener of [...listeners]) listener();
}

function attach(): () => void {
  const query = mediaQuery(LIGHT_QUERY);
  const onMediaChange = (event: MediaQueryListEvent) => {
    prefersLight = event.matches;
    if (recompute()) {
      applyTheme(snapshot.resolved);
      emit();
    }
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== THEME_STORAGE_KEY) return;
    if (syncFromEnvironment()) {
      applyTheme(snapshot.resolved);
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

function getSnapshot(): ThemeSnapshot {
  // Nobody is listening: read fresh so the first render matches storage.
  if (listeners.size === 0) syncFromEnvironment();
  return snapshot;
}

function getServerSnapshot(): ThemeSnapshot {
  return snapshot;
}

/** Persist and apply a theme preference (works outside React too). */
export function setTheme(next: ThemePreference): void {
  preference = next;
  writeLocal(THEME_STORAGE_KEY, next);
  recompute();
  applyTheme(snapshot.resolved);
  emit();
}

export function useTheme(): ThemeState {
  const current = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return useMemo(() => ({ theme: current.theme, resolved: current.resolved, setTheme }), [current]);
}

/** Keeps `<html data-theme>` in sync with the preference. Mount once near the root. */
export function ThemeProvider({ children }: { children?: ReactNode }) {
  const { resolved } = useTheme();
  useLayoutEffect(() => {
    applyTheme(resolved);
  }, [resolved]);
  return createElement(Fragment, null, children);
}
