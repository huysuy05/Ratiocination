import { Bookmark, Check, Eye, X } from "lucide-react";
import { describe, expect, it } from "vitest";
import type { FeedbackAction, PaperState } from "../api/types";
import { ACTION_EXIT_DIRECTION, ACTION_TO_STATE, PAPER_STATES, READ_META, STATE_META, stateForAction } from "./states";

describe("ACTION_TO_STATE", () => {
  it("matches SPEC §11", () => {
    expect(ACTION_TO_STATE).toEqual({ relevant: "relevant", not_relevant: "not_relevant", save: "saved" });
  });

  it('"open" changes nothing; every state action has an exit direction', () => {
    expect(stateForAction("open")).toBeNull();
    expect(stateForAction("save")).toBe("saved");
    const actions: FeedbackAction[] = ["relevant", "not_relevant", "save"];
    for (const action of actions) expect(ACTION_EXIT_DIRECTION).toHaveProperty(action);
    expect(ACTION_EXIT_DIRECTION).toEqual({ relevant: "right", not_relevant: "left", save: "up" });
  });
});

describe("STATE_META", () => {
  it.each<[PaperState, string, string, unknown]>([
    ["relevant", "Relevant", "--relevant", Check],
    ["not_relevant", "Not relevant", "--not-relevant", X],
    ["saved", "Saved", "--save", Bookmark],
  ])("%s → %s / %s", (state, label, token, icon) => {
    expect(STATE_META[state]).toMatchObject({ state, label, token, color: `var(${token})`, icon });
  });

  it("lists every state once", () => {
    expect([...PAPER_STATES].sort()).toEqual(Object.keys(STATE_META).sort());
  });

  it("describes the read mark separately", () => {
    expect(READ_META).toMatchObject({ label: "Read", token: "--read", color: "var(--read)", icon: Eye });
  });
});
