/**
 * The daily deck (UI.md §7): swipe or press keys to rate papers; undo flies a card back; the detail
 * sheet opens from `?paper=`. Feed states: needs_setup, building (radar sweep over the dimmed
 * previous deck), ready, empty, not_built, plus a failed-build banner and the "Deck complete" end.
 */
import { AnimatePresence } from "motion/react";
import { CircleAlert, Keyboard, Settings2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { errorMessage, getConflictJob } from "../api/client";
import { useFeed, useFeedback, useProject, useRebuildFeed, useSetRead, useUndo } from "../api/hooks";
import type { RecommendationItem, SlateMode } from "../api/types";
import { useProjectIdParam, usePaperSheetParam } from "../app/params";
import { paths } from "../app/paths";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Dialog } from "../components/ui/Dialog";
import { EmptyState } from "../components/ui/EmptyState";
import { Kbd } from "../components/ui/Kbd";
import { RadarBackdrop, RadarSweep } from "../components/ui/RadarSweep";
import { Skeleton } from "../components/ui/Skeleton";
import { ActionBar } from "../features/feed/ActionBar";
import { DeckCardContent } from "../features/feed/DeckCardContent";
import { DeckComplete } from "../features/feed/DeckComplete";
import { DeckHeader } from "../features/feed/DeckHeader";
import { SwipeCard, type Decision, type SwipeCardHandle } from "../features/feed/SwipeCard";
import { PaperDetailSheet } from "../features/paper/PaperDetailSheet";
import { useHotkeys, useHotkeySuspension } from "../lib/hotkeys";
import { ACTION_TO_STATE, STATE_META, type ExitDirection } from "../lib/states";

const SHORTCUTS: [string, string][] = [
  ["→", "Relevant"],
  ["←", "Not relevant"],
  ["↑ or S", "Save"],
  ["R", "Mark read / unread"],
  ["Z or Backspace", "Undo"],
  ["Space or Enter", "Open details"],
  ["?", "This help"],
];

interface Exit {
  paperId: number;
  direction: ExitDirection;
}

export default function FeedPage() {
  const projectId = useProjectIdParam();
  const project = useProject(projectId);
  const feed = useFeed(projectId);
  const feedback = useFeedback(projectId);
  const undo = useUndo(projectId);
  const setRead = useSetRead();
  const rebuild = useRebuildFeed(projectId);
  const sheet = usePaperSheetParam();
  // One handle per card, keyed by paper id. A single shared ref would break: the card that just
  // left stays mounted for its exit animation and, when it unmounts, React would clear the ref the
  // new top card had already taken — every later button and arrow key then did nothing.
  const cards = useRef(new Map<number, SwipeCardHandle>());
  const completeHeading = useRef<HTMLHeadingElement>(null);
  const deckRegion = useRef<HTMLDivElement>(null);
  const exits = useRef<Exit[]>([]);
  const [returning, setReturning] = useState<Exit | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [confirmMode, setConfirmMode] = useState<SlateMode | null>(null);
  useHotkeySuspension(helpOpen || confirmMode !== null);

  const data = feed.data;
  const slate = data?.slate ?? null;
  const items: RecommendationItem[] = slate?.items ?? [];
  const undecided = items.filter((item) => item.state === null);
  const visible = undecided.slice(0, 3);
  const top = visible[0] ?? null;
  const status = data?.status;
  const building = status === "building";

  // A new slate starts a fresh undo history.
  const slateId = slate?.id ?? null;
  const clearUndo = undo.clear;
  useEffect(() => {
    exits.current = [];
    setReturning(null);
    clearUndo();
  }, [slateId, clearUndo]);

  // Focus follows the deck: the new top card, or the "Deck complete" heading.
  const topId = top?.paper.id ?? null;
  useEffect(() => {
    const active = document.activeElement;
    const inDeck = active === document.body || active === null || (deckRegion.current?.contains(active) ?? false);
    if (!inDeck) return;
    if (topId !== null) cards.current.get(topId)?.focus();
    else completeHeading.current?.focus();
  }, [topId]);

  const runUndo = useCallback(() => {
    if (!undo.canUndo) return;
    const last = exits.current.pop() ?? null;
    setReturning(last);
    undo.mutate(undefined, {
      onSuccess: (result) => {
        if (result) toast("Undone");
      },
    });
  }, [undo]);

  const onDecide = (item: RecommendationItem) => (action: Decision, direction: ExitDirection) => {
    exits.current.push({ paperId: item.paper.id, direction });
    setReturning(null);
    feedback.mutate(
      { paper_id: item.paper.id, action, slate_id: slate?.id ?? null, context: { source: "deck" }, exitDirection: direction },
      {
        onSuccess: () => {
          toast(STATE_META[ACTION_TO_STATE[action]].done, { action: { label: "Undo", onClick: runUndo }, duration: 4000 });
        },
      },
    );
  };

  const decide = (action: Decision) => {
    if (top) cards.current.get(top.paper.id)?.commit(action);
  };
  const toggleRead = () => {
    if (!top) return;
    const read = top.paper.read_at === null;
    setRead.mutate({ paperId: top.paper.id, read }, { onSuccess: () => toast(read ? "Marked as read" : "Marked as unread") });
  };
  const openTop = () => {
    if (top) sheet.openPaper(top.paper.id);
  };

  const startRebuild = (mode: SlateMode, prepare = false) => {
    rebuild.mutate(
      { mode, prepare },
      {
        onError: (error) => {
          if (getConflictJob(error)) toast("A deck is already being built");
          else toast.error(errorMessage(error, "Could not start the deck build"));
        },
      },
    );
  };
  const askRebuild = (mode: SlateMode) => {
    if (undecided.length > 0) setConfirmMode(mode);
    else startRebuild(mode);
  };

  useHotkeys(
    "deck",
    {
      ArrowRight: () => decide("relevant"),
      ArrowLeft: () => decide("not_relevant"),
      ArrowUp: () => decide("save"),
      s: () => decide("save"),
      z: runUndo,
      Backspace: runUndo,
      r: toggleRead,
      " ": openTop,
      Enter: openTop,
      "?": () => setHelpOpen(true),
    },
    { enabled: status === "ready" || (building && items.length > 0) },
  );

  if (projectId === null) return null;

  let body;
  if (feed.isPending) {
    body = (
      <div className="mx-auto flex w-full max-w-[560px] flex-col gap-5">
        <Skeleton className="h-16" />
        <Skeleton className="h-[max(340px,calc(100dvh-370px))] rounded-card md:h-[clamp(460px,62vh,620px)]" />
      </div>
    );
  } else if (feed.isError) {
    body = <EmptyState title="Could not load the deck" description={feed.error.message} actions={<Button onClick={() => void feed.refetch()}>Try again</Button>} />;
  } else if (status === "needs_setup") {
    body = (
      <EmptyState
        size="lg"
        icon={<Settings2 aria-hidden="true" />}
        title="Tell Ratiocination where to look"
        description="Add the arXiv categories your field publishes in, and the first deck builds automatically."
        actions={
          <Button asChild variant="primary">
            <Link to={paths.projectSettings(projectId)}>Add categories</Link>
          </Button>
        }
      />
    );
  } else if (status === "not_built") {
    body = (
      <EmptyState
        size="lg"
        title="No deck yet"
        description="Fetch the latest papers from your categories and rank them against your question."
        actions={
          <Button variant="primary" loading={rebuild.isPending} onClick={() => startRebuild("daily", true)}>
            Build my first deck
          </Button>
        }
      />
    );
  } else if (building && visible.length === 0) {
    body = (
      <div className="mx-auto flex w-full max-w-[560px] flex-col items-center py-12">
        <RadarSweep size="lg" title="Building your deck" message={data?.job?.message ?? "Fetching new papers…"} progress={data?.job?.progress} label="Deck build progress" />
      </div>
    );
  } else if (status === "empty") {
    body = (
      <EmptyState
        size="lg"
        title="Nothing new to show"
        description={slate?.message ?? data?.message ?? "No new papers matched today."}
        actions={
          <>
            <Button asChild>
              <Link to={paths.projectSettings(projectId)}>Adjust sources</Link>
            </Button>
            <Button variant="primary" loading={rebuild.isPending} onClick={() => startRebuild("more")}>
              Look again
            </Button>
          </>
        }
      />
    );
  } else if (visible.length === 0) {
    body = <DeckComplete ref={completeHeading} projectId={projectId} items={items} onMore={() => startRebuild("more")} busy={rebuild.isPending} />;
  } else {
    body = (
      <div className="mx-auto flex w-full max-w-[560px] flex-col gap-6">
        <div className="relative h-[max(340px,calc(100dvh-370px))] md:h-[clamp(460px,62vh,620px)]" aria-label="Deck">
          <AnimatePresence initial={false}>
            {visible.map((item, depth) => (
              <SwipeCard
                key={item.paper.id}
                ref={(handle) => {
                  const id = item.paper.id;
                  if (handle) cards.current.set(id, handle);
                  return () => {
                    if (cards.current.get(id) === handle) cards.current.delete(id);
                  };
                }}
                item={item}
                depth={depth}
                enterFrom={returning?.paperId === item.paper.id ? returning.direction : null}
                disabled={building}
                onDecide={onDecide(item)}
                ariaLabel={`Card ${items.indexOf(item) + 1} of ${items.length}: ${item.paper.title}`}
              >
                <DeckCardContent item={item} onWhy={() => sheet.openPaper(item.paper.id)} />
              </SwipeCard>
            ))}
          </AnimatePresence>
          {building ? (
            <div className="absolute inset-0 z-30 grid place-items-center rounded-card bg-bg/70 backdrop-blur-sm">
              <RadarSweep title="Building a new deck" message={data?.job?.message ?? "Working…"} progress={data?.job?.progress} label="Deck build progress" />
            </div>
          ) : null}
        </div>
        <ActionBar
          onDecide={decide}
          onUndo={runUndo}
          onToggleRead={toggleRead}
          canUndo={undo.canUndo}
          read={top?.paper.read_at !== null && top?.paper.read_at !== undefined}
          disabled={building || top === null}
        />
        <button type="button" onClick={() => setHelpOpen(true)} className="mx-auto hidden items-center gap-1.5 text-[12.5px] text-faint hover:text-muted md:inline-flex">
          <Keyboard aria-hidden="true" className="size-3.5" />
          Keyboard shortcuts <Kbd>?</Kbd>
        </button>
      </div>
    );
  }

  const failed = data?.last_job?.status === "failed" ? data.last_job : null;
  return (
    <section aria-labelledby="page-title" className="relative isolate flex w-full flex-col">
      <RadarBackdrop className="-z-10 opacity-60" />
      <DeckHeader
        projectName={project.data?.name}
        generatedAt={slate?.generated_at ?? null}
        items={items}
        busy={building || rebuild.isPending}
        onRebuild={() => askRebuild("daily")}
        onMore={() => askRebuild("more")}
      />
      {failed && !building ? (
        <div role="alert" className="mx-auto mb-5 flex w-full max-w-[560px] items-start gap-3 rounded-panel border border-not-relevant/30 bg-not-relevant/8 px-4 py-3">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-not-relevant" />
          <p className="min-w-0 flex-1 text-[13.5px] text-text">
            The last deck build failed: {failed.error_message ?? "unknown error"}
          </p>
          <Button size="sm" onClick={() => startRebuild("daily")} loading={rebuild.isPending}>
            Retry
          </Button>
        </div>
      ) : null}
      <div ref={deckRegion}>{body}</div>
      <p className="sr-only" aria-live="polite">
        {status === "ready" && items.length > 0 ? `${undecided.length} cards left` : ""}
      </p>
      <PaperDetailSheet paperId={sheet.paperId} projectId={projectId} slateId={slate?.id ?? null} onClose={sheet.closePaper} />
      <ConfirmDialog
        open={confirmMode !== null}
        onOpenChange={(open) => !open && setConfirmMode(null)}
        title={confirmMode === "more" ? "Load more papers?" : "Rebuild today's deck?"}
        description={`This replaces the current deck (${undecided.length} card${undecided.length === 1 ? "" : "s"} left). Papers you skip may come back later.`}
        confirmLabel={confirmMode === "more" ? "Load more" : "Rebuild"}
        onConfirm={() => {
          if (confirmMode) startRebuild(confirmMode);
          setConfirmMode(null);
        }}
      />
      <Dialog open={helpOpen} onOpenChange={setHelpOpen} title="Keyboard shortcuts" size="sm">
        <dl className="flex flex-col gap-2">
          {SHORTCUTS.map(([keys, label]) => (
            <div key={label} className="flex items-center justify-between gap-4 text-[14px]">
              <dt className="text-muted">{label}</dt>
              <dd>
                <Kbd>{keys}</Kbd>
              </dd>
            </div>
          ))}
        </dl>
      </Dialog>
    </section>
  );
}
