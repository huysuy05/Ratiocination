/**
 * Paper states, the read mark and feedback actions. Single source for how a state looks everywhere:
 * progress bar, toasts, announcements, graph fills and summaries (UI.md §2).
 */
import { Bookmark, Check, Eye, X, type LucideIcon } from "lucide-react";
import type { FeedbackAction, PaperState } from "../api/types";

/** Feedback actions that change the paper's state (everything except "open"). */
export type StateAction = Exclude<FeedbackAction, "open">;

/** SPEC §11: the state each action sets. "open" changes nothing. */
export const ACTION_TO_STATE: Readonly<Record<StateAction, PaperState>> = {
  relevant: "relevant",
  not_relevant: "not_relevant",
  save: "saved",
};

/** The state an action sets, or null for "open" (and unknown values). */
export function stateForAction(action: FeedbackAction): PaperState | null {
  return action === "open" ? null : (ACTION_TO_STATE[action] ?? null);
}

export function isStateAction(action: FeedbackAction): action is StateAction {
  return action !== "open";
}

export interface MarkMeta {
  /** Short label, e.g. "Relevant". */
  label: string;
  /** Past-tense confirmation used in toasts/announcements, e.g. "Marked relevant". */
  done: string;
  /** CSS custom property name of the colour, e.g. "--relevant". */
  token: string;
  /** `var(--token)`, ready for style props / Cytoscape. */
  color: string;
  icon: LucideIcon;
}

export interface StateMeta extends MarkMeta {
  state: PaperState;
}

function meta(state: PaperState, label: string, done: string, token: string, icon: LucideIcon): StateMeta {
  return { state, label, done, token, color: `var(${token})`, icon };
}

/** UI.md §2 state presentation. */
export const STATE_META: Readonly<Record<PaperState, StateMeta>> = {
  relevant: meta("relevant", "Relevant", "Marked relevant", "--relevant", Check),
  not_relevant: meta("not_relevant", "Not relevant", "Marked not relevant", "--not-relevant", X),
  saved: meta("saved", "Saved", "Saved", "--save", Bookmark),
};

/** The read mark (per user, independent of projects and states). */
export const READ_META: MarkMeta = {
  label: "Read",
  done: "Marked as read",
  token: "--read",
  color: "var(--read)",
  icon: Eye,
};

/** Display order for summaries (e.g. "today's decisions by type"). */
export const PAPER_STATES: readonly PaperState[] = ["relevant", "saved", "not_relevant"];

/** Where a card leaves the deck (UI.md §7), recorded on the undo stack so Undo flies it back in. */
export type ExitDirection = "left" | "right" | "up" | "fade";

/** Default exit per action: relevant → right, not relevant → left, save → up. */
export const ACTION_EXIT_DIRECTION: Readonly<Record<StateAction, ExitDirection>> = {
  relevant: "right",
  not_relevant: "left",
  save: "up",
};
