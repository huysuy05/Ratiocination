import { describe, expect, it, vi } from "vitest";
import {
  readLocal,
  readLocalId,
  readLocalJSON,
  readSession,
  removeLocal,
  STORAGE_KEYS,
  writeLocal,
  writeLocalJSON,
  writeSession,
} from "./storage";

describe("storage helpers", () => {
  it("exposes the documented keys", () => {
    expect(STORAGE_KEYS).toEqual({
      theme: "rr.theme",
      motion: "rr.motion",
      lastProjectId: "rr.lastProjectId",
    });
  });

  it("reads, writes and removes raw strings", () => {
    expect(readLocal("rr.x")).toBeNull();
    expect(writeLocal("rr.x", "1")).toBe(true);
    expect(readLocal("rr.x")).toBe("1");
    writeLocal("rr.x", null);
    expect(readLocal("rr.x")).toBeNull();
    writeLocal("rr.y", "2");
    removeLocal("rr.y");
    expect(localStorage.getItem("rr.y")).toBeNull();
    writeSession("rr.s", "a");
    expect(readSession("rr.s")).toBe("a");
  });

  it("parses ids strictly", () => {
    localStorage.setItem("rr.id", "42");
    expect(readLocalId("rr.id")).toBe(42);
    for (const bad of ["0", "-1", "4.2", "abc", "", "12abc"]) {
      localStorage.setItem("rr.id", bad);
      expect(readLocalId("rr.id")).toBeNull();
    }
  });

  it("reads JSON with a fallback and an optional validator", () => {
    writeLocalJSON("rr.json", { a: 1 });
    expect(readLocalJSON("rr.json", null)).toEqual({ a: 1 });
    localStorage.setItem("rr.json", "{broken");
    expect(readLocalJSON("rr.json", "fallback")).toBe("fallback");
    writeLocalJSON("rr.json", 5);
    const isString = (value: unknown): value is string => typeof value === "string";
    expect(readLocalJSON("rr.json", "x", isString)).toBe("x");
  });

  it("never throws when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(readLocal("rr.theme")).toBeNull();
    expect(writeLocal("rr.theme", "dark")).toBe(false);
    expect(readSession("rr.mockScenario")).toBeNull();
    expect(readLocalJSON("rr.json", 1)).toBe(1);
  });
});
