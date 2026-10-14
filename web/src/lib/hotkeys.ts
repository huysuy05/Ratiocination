/**
 * Keyboard shortcuts (UI.md §6, FROZEN).
 *
 * `useHotkeys(scope, map, options)` binds keys while the component is mounted. Scopes: "deck"
 * (FeedPage) and "sheet" (PaperDetailSheet). Only the highest-priority scope with a live binding
 * receives keys, so opening the sheet suspends the deck. `useHotkeySuspension(true)` (e.g. while a
 * confirm or help dialog is open) suspends every scope.
 *
 * Map keys are `KeyboardEvent.key` values, matched case-insensitively for letters: "ArrowLeft",
 * "s", "?", "Backspace", "Enter", "Escape", " " — aliases "Space", "Esc", "Left", "Right", "Up",
 * "Down" are accepted. A handled key gets `preventDefault()`.
 *
 * Ignored events: meta/ctrl/alt modifiers, IME composition, `defaultPrevented`, targets inside
 * input/textarea/select/[contenteditable]; Space/Enter on buttons, links and
 * [role=button|menuitem|tab|switch] (they keep their native activation); arrow keys inside
 * [role=menu|tablist|slider|radiogroup|listbox]. Auto-repeat (key held down) is ignored unless
 * `allowRepeat` is set, so holding a key never swipes through the deck.
 */
import { useEffect, useLayoutEffect, useRef } from "react";

export type HotkeyScope = "deck" | "sheet";

/** Higher wins: an active sheet suspends the deck. */
export const HOTKEY_SCOPE_PRIORITY: Readonly<Record<HotkeyScope, number>> = { deck: 0, sheet: 1 };

export type HotkeyHandler = (event: KeyboardEvent) => void;
export type HotkeyMap = Readonly<Record<string, HotkeyHandler | undefined>>;

export interface HotkeyOptions {
  /** Default true. A disabled registration does not suspend lower scopes. */
  enabled?: boolean;
  /** Fire on auto-repeat keydown events too (default false). */
  allowRepeat?: boolean;
}

const KEY_ALIASES: Readonly<Record<string, string>> = {
  space: " ",
  spacebar: " ",
  esc: "escape",
  left: "arrowleft",
  right: "arrowright",
  up: "arrowup",
  down: "arrowdown",
  return: "enter",
  del: "delete",
};

/** Canonical form used for matching: lowercase, with aliases resolved ("Space" → " "). */
export function normalizeKey(key: string): string {
  if (key === " ") return " ";
  const lower = key.toLowerCase();
  return KEY_ALIASES[lower] ?? lower;
}

const ARROW_KEYS = new Set(["arrowleft", "arrowright", "arrowup", "arrowdown"]);

/** Space/Enter keep their native meaning on these. */
const ACTIVATABLE_SELECTOR = [
  "button",
  "a[href]",
  "summary",
  "[role='button']",
  "[role='link']",
  "[role='menuitem']",
  "[role='menuitemcheckbox']",
  "[role='menuitemradio']",
  "[role='tab']",
  "[role='switch']",
  "[role='option']",
].join(", ");

/** Arrow keys keep their native meaning inside these widgets. */
const ARROW_WIDGET_SELECTOR = [
  "[role='menu']",
  "[role='menubar']",
  "[role='tablist']",
  "[role='slider']",
  "[role='radiogroup']",
  "[role='listbox']",
].join(", ");

function isEditable(element: Element): boolean {
  if (element.closest("input, textarea, select")) return true;
  const editable = element.closest("[contenteditable]");
  if (!editable) return false;
  const value = (editable.getAttribute("contenteditable") ?? "").toLowerCase();
  return value !== "false";
}

/** True when a keydown must not trigger a shortcut (UI.md §6 ignore rules). */
export function shouldIgnoreHotkey(event: KeyboardEvent): boolean {
  if (event.metaKey || event.ctrlKey || event.altKey) return true;
  if (event.isComposing || event.keyCode === 229) return true;
  if (event.defaultPrevented) return true;
  const target = event.target;
  if (!(target instanceof Element)) return false;
  if (isEditable(target)) return true;
  const key = normalizeKey(event.key);
  if ((key === " " || key === "enter") && target.closest(ACTIVATABLE_SELECTOR)) return true;
  if (ARROW_KEYS.has(key) && target.closest(ARROW_WIDGET_SELECTOR)) return true;
  return false;
}

// ---- registry (one window listener for all scopes) ----

interface Registration {
  scope: HotkeyScope;
  order: number;
  map: { current: HotkeyMap };
  options: { current: HotkeyOptions };
}

const registrations = new Set<Registration>();
let suspensions = 0;
let nextOrder = 0;
let listening = false;

/** The scope that currently receives keys, or null (none registered or suspended). */
export function getActiveHotkeyScope(): HotkeyScope | null {
  if (suspensions > 0) return null;
  let active: HotkeyScope | null = null;
  for (const registration of registrations) {
    if (active === null || HOTKEY_SCOPE_PRIORITY[registration.scope] > HOTKEY_SCOPE_PRIORITY[active]) {
      active = registration.scope;
    }
  }
  return active;
}

function findHandler(map: HotkeyMap, key: string): HotkeyHandler | undefined {
  for (const [name, handler] of Object.entries(map)) {
    if (handler && normalizeKey(name) === key) return handler;
  }
  return undefined;
}

function handleKeyDown(event: KeyboardEvent): void {
  const scope = getActiveHotkeyScope();
  if (scope === null || shouldIgnoreHotkey(event)) return;
  const key = normalizeKey(event.key);
  const candidates = [...registrations]
    .filter((registration) => registration.scope === scope)
    .sort((a, b) => b.order - a.order); // most recently mounted first
  for (const registration of candidates) {
    const handler = findHandler(registration.map.current, key);
    if (!handler) continue;
    event.preventDefault();
    if (event.repeat && !registration.options.current.allowRepeat) return;
    handler(event);
    return;
  }
}

function syncListener(): void {
  if (typeof window === "undefined") return;
  const needed = registrations.size > 0;
  if (needed && !listening) {
    window.addEventListener("keydown", handleKeyDown);
    listening = true;
  } else if (!needed && listening) {
    window.removeEventListener("keydown", handleKeyDown);
    listening = false;
  }
}

/** Bind `map` in `scope` while mounted (and `options.enabled !== false`). The map may change every render. */
export function useHotkeys(scope: HotkeyScope, map: HotkeyMap, options: HotkeyOptions = {}): void {
  const mapRef = useRef(map);
  const optionsRef = useRef(options);
  useLayoutEffect(() => {
    mapRef.current = map;
    optionsRef.current = options;
  });
  const enabled = options.enabled ?? true;
  useEffect(() => {
    if (!enabled) return;
    const registration: Registration = { scope, order: ++nextOrder, map: mapRef, options: optionsRef };
    registrations.add(registration);
    syncListener();
    return () => {
      registrations.delete(registration);
      syncListener();
    };
  }, [scope, enabled]);
}

/** Suspend every hotkey scope while `active` (modal dialogs layered over the deck or sheet). */
export function useHotkeySuspension(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    suspensions += 1;
    return () => {
      suspensions -= 1;
    };
  }, [active]);
}

/** Tests only: drop every registration and suspension. */
export function resetHotkeys(): void {
  registrations.clear();
  suspensions = 0;
  syncListener();
}

const KEY_LABELS: Readonly<Record<string, string>> = {
  arrowleft: "←",
  arrowright: "→",
  arrowup: "↑",
  arrowdown: "↓",
  " ": "Space",
  enter: "Enter",
  escape: "Esc",
  backspace: "⌫",
};

/** Display label for a key, e.g. "ArrowLeft" → "←", "s" → "S". */
export function formatHotkey(key: string): string {
  const normalized = normalizeKey(key);
  return KEY_LABELS[normalized] ?? (normalized.length === 1 ? normalized.toUpperCase() : key);
}
