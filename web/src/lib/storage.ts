/**
 * Safe Web Storage helpers. Storage can be unavailable or throw (private windows, blocked
 * site data, quota), so every access is wrapped in try/catch and falls back gracefully. Values are
 * stored as raw strings unless the *JSON helpers are used.
 */

/** Every storage key the app uses. Keys are shared with the pre-paint script in index.html. */
export const STORAGE_KEYS = {
  /** localStorage: "system" | "dark" | "light" (default dark). */
  theme: "rr.theme",
  /** localStorage: "system" | "reduced" | "full" (default system). */
  motion: "rr.motion",
  /** localStorage: id of the last opened project (written by the AppShell). */
  lastProjectId: "rr.lastProjectId",
} as const;

export type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS];

type StorageKind = "local" | "session";

function getStorage(kind: StorageKind): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

function read(kind: StorageKind, key: string): string | null {
  try {
    return getStorage(kind)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function write(kind: StorageKind, key: string, value: string | null): boolean {
  try {
    const storage = getStorage(kind);
    if (!storage) return false;
    if (value === null) storage.removeItem(key);
    else storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** Read a raw string from localStorage (null when missing or unavailable). */
export function readLocal(key: string): string | null {
  return read("local", key);
}

/** Write a raw string to localStorage; `null` removes the key. Returns false when storage failed. */
export function writeLocal(key: string, value: string | null): boolean {
  return write("local", key, value);
}

export function removeLocal(key: string): boolean {
  return write("local", key, null);
}

/** Read a raw string from sessionStorage (null when missing or unavailable). */
export function readSession(key: string): string | null {
  return read("session", key);
}

/** Write a raw string to sessionStorage; `null` removes the key. Returns false when storage failed. */
export function writeSession(key: string, value: string | null): boolean {
  return write("session", key, value);
}

export function removeSession(key: string): boolean {
  return write("session", key, null);
}

/**
 * Read and JSON-parse a localStorage value. Returns `fallback` when missing, unparsable, or when
 * `validate` rejects the parsed value.
 */
export function readLocalJSON<T>(key: string, fallback: T, validate?: (value: unknown) => value is T): T {
  const raw = readLocal(key);
  if (raw === null) return fallback;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (validate) return validate(parsed) ? parsed : fallback;
    return parsed as T;
  } catch {
    return fallback;
  }
}

/** JSON-serialize a value into localStorage. Returns false when storage failed. */
export function writeLocalJSON(key: string, value: unknown): boolean {
  try {
    return writeLocal(key, JSON.stringify(value));
  } catch {
    return false;
  }
}

/** Parse a stored positive integer id (e.g. rr.lastProjectId); null when absent or malformed. */
export function readLocalId(key: string): number | null {
  const raw = readLocal(key);
  if (raw === null || !/^[1-9]\d{0,15}$/.test(raw.trim())) return null;
  const id = Number(raw.trim());
  return Number.isSafeInteger(id) ? id : null;
}
