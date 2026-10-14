/**
 * Deck actions for one paper outside the deck (UI.md §8.8): Not relevant / Save / Relevant with the
 * current state pressed, Undo of the paper's last action and the read toggle.
 */
import clsx from "clsx";
import { Bookmark, Check, Eye, RotateCcw, X } from "lucide-react";
import { toast } from "sonner";
import { useFeedback, useSetRead, useUndo } from "../../api/hooks";
import type { FeedbackSource, PaperDetail } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { ACTION_TO_STATE, STATE_META, type StateAction } from "../../lib/states";

const BUTTONS: { action: StateAction; label: string; icon: typeof Check }[] = [
  { action: "not_relevant", label: "Not relevant", icon: X },
  { action: "save", label: "Save", icon: Bookmark },
  { action: "relevant", label: "Relevant", icon: Check },
];

export interface PaperActionsProps {
  paper: PaperDetail;
  projectId: number;
  slateId?: number | null;
  source: FeedbackSource;
  /** Called after a state action succeeded (the sheet closes). */
  onDecided?: () => void;
}

export function usePaperActions({ paper, projectId, slateId, source, onDecided }: PaperActionsProps) {
  const feedback = useFeedback(projectId);
  const undo = useUndo(projectId);
  const setRead = useSetRead();
  const decide = (action: StateAction) =>
    feedback.mutate(
      { paper_id: paper.id, action, slate_id: slateId ?? paper.recommendation?.slate_id ?? null, context: { source } },
      {
        onSuccess: () => {
          toast(STATE_META[ACTION_TO_STATE[action]].done);
          onDecided?.();
        },
      },
    );
  const undoLast = () => {
    if (paper.last_interaction_id !== null) undo.mutate({ interaction_id: paper.last_interaction_id }, { onSuccess: () => toast("Undone") });
  };
  const toggleRead = () => setRead.mutate({ paperId: paper.id, read: paper.read_at === null });
  return { decide, undoLast, toggleRead, busy: feedback.isPending || undo.isPending };
}

export function PaperActions(props: PaperActionsProps) {
  const { paper } = props;
  const { decide, undoLast, toggleRead, busy } = usePaperActions(props);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {BUTTONS.map(({ action, label, icon: Icon }) => {
        const state = ACTION_TO_STATE[action];
        const pressed = paper.state === state;
        const color = STATE_META[state].color;
        return (
          <button
            key={action}
            type="button"
            aria-pressed={pressed}
            disabled={busy}
            onClick={() => decide(action)}
            style={{ ["--tone" as string]: color }}
            className={clsx(
              "inline-flex h-10 items-center gap-2 rounded-button border px-3.5 text-[13.5px] font-medium transition-colors pointer-coarse:h-11",
              pressed ? "border-(--tone)/50 bg-(--tone)/14 text-text" : "border-border text-muted hover:border-(--tone)/40 hover:text-text",
              "disabled:opacity-50",
            )}
          >
            <Icon aria-hidden="true" className="size-4 text-(--tone)" />
            {label}
          </button>
        );
      })}
      <div className="ml-auto flex items-center gap-1">
        {paper.last_interaction_id !== null ? (
          <Button size="sm" variant="ghost" leadingIcon={<RotateCcw aria-hidden="true" />} onClick={undoLast} disabled={busy}>
            Undo
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" aria-pressed={paper.read_at !== null} leadingIcon={<Eye aria-hidden="true" />} onClick={toggleRead}>
          {paper.read_at !== null ? "Read" : "Mark read"}
        </Button>
      </div>
    </div>
  );
}
