/**
 * Paper detail sheet (UI.md §5, §8): open iff `paperId !== null` (driven by `?paper=<id>`). Posts
 * the `open` feedback action once per opened paper when there is a project. While open it holds the
 * "sheet" hotkey scope (suspending the deck): → relevant, ← not relevant, ↑/S save, R read, Z undo;
 * a decision closes the sheet.
 */
import { useEffect, useRef, type JSX } from "react";
import { useFeedback, usePaper } from "../../api/hooks";
import { usePaperSheetParam } from "../../app/params";
import { EmptyState } from "../../components/ui/EmptyState";
import { Sheet } from "../../components/ui/Sheet";
import { Skeleton, SkeletonText } from "../../components/ui/Skeleton";
import { useHotkeys } from "../../lib/hotkeys";
import { PaperActions, usePaperActions } from "./PaperActions";
import { PaperDetailContent } from "./PaperDetailContent";
import type { FeedbackSource, PaperDetail } from "../../api/types";

export interface PaperDetailSheetProps {
  paperId: number | null;
  projectId: number | null;
  slateId?: number | null;
  /** Where the sheet was opened from (recorded with its feedback; default "detail"). */
  source?: FeedbackSource;
  onClose: () => void;
}

function SheetHotkeys({
  paper,
  projectId,
  slateId,
  source,
  onClose,
}: {
  paper: PaperDetail;
  projectId: number;
  slateId: number | null;
  source: FeedbackSource;
  onClose: () => void;
}) {
  const { decide, undoLast, toggleRead } = usePaperActions({ paper, projectId, slateId, source, onDecided: onClose });
  useHotkeys("sheet", {
    ArrowRight: () => decide("relevant"),
    ArrowLeft: () => decide("not_relevant"),
    ArrowUp: () => decide("save"),
    s: () => decide("save"),
    r: toggleRead,
    z: undoLast,
  });
  return null;
}

export function PaperDetailSheet({ paperId, projectId, slateId = null, source = "detail", onClose }: PaperDetailSheetProps): JSX.Element {
  const open = paperId !== null;
  const paper = usePaper(paperId, projectId, { enabled: open });
  const feedback = useFeedback(projectId);
  const { openPaper } = usePaperSheetParam();
  const posted = useRef<number | null>(null);
  // Holds the sheet scope even before the paper loads, so deck keys never act behind the sheet.
  useHotkeys("sheet", {}, { enabled: open });

  const mutateFeedback = feedback.mutate;
  useEffect(() => {
    if (!open || projectId === null) {
      posted.current = null;
      return;
    }
    if (posted.current === paperId) return;
    posted.current = paperId;
    mutateFeedback({ paper_id: paperId, action: "open", slate_id: slateId, context: { source } });
  }, [open, paperId, projectId, slateId, source, mutateFeedback]);

  const data = paper.data && paper.data.id === paperId ? paper.data : null;
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={data?.title ?? (paper.isError ? "Paper not found" : "Loading paper")}
      hideTitle
      size="md"
      footer={data && projectId !== null ? <PaperActions paper={data} projectId={projectId} slateId={slateId} source={source} onDecided={onClose} /> : undefined}
    >
      {data && projectId !== null && open ? <SheetHotkeys paper={data} projectId={projectId} slateId={slateId} source={source} onClose={onClose} /> : null}
      {data ? (
        <PaperDetailContent paper={data} projectId={projectId} onOpenPaper={openPaper} headingLevel="none" />
      ) : paper.isError ? (
        <EmptyState title="Paper not found" description={paper.error.message} />
      ) : (
        <div className="flex flex-col gap-4" aria-busy="true">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-16" />
          <SkeletonText lines={6} />
        </div>
      )}
    </Sheet>
  );
}
