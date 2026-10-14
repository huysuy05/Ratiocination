/**
 * One deck card with swipe physics (UI.md §7).
 *
 * - Stack: depth 0 is the top card; depth 1–2 sit behind it (scale 0.96 / 0.92, y +12 / +24,
 *   dimmed) and spring forward when the top card leaves.
 * - Drag (top card only): rotate = x / 18 (±14°), lift 1.02; stamps fade in with distance — right
 *   RELEVANT, left NOT RELEVANT, up SAVE — and the border tints toward the action colour. Commit when
 *   |x| > 140 px or |vx| > 650 (relevant / not relevant), or y < −130 px or vy < −650 (save), along
 *   the dominant axis; otherwise spring back.
 * - `commit(action)` (drag, buttons and keys alike) flies the card off in the action's direction
 *   (~320 ms; a 150 ms fade under reduced motion) and then calls `onDecide`.
 * - `enterFrom` (Undo): the card flies back in from the side it left by.
 */
import clsx from "clsx";
import { animate, motion, useMotionValue, useTransform, type MotionValue, type PanInfo } from "motion/react";
import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, type ReactNode } from "react";
import type { RecommendationItem } from "../../api/types";
import { SPRING, SPRING_GENTLE, useMotionPreference } from "../../lib/motion";
import { ACTION_EXIT_DIRECTION, STATE_META, type ExitDirection, type StateAction } from "../../lib/states";

export type Decision = StateAction;

export interface SwipeCardHandle {
  /** Fly the card off for `action`, then report it (ignored while already leaving). */
  commit: (action: Decision) => void;
  focus: () => void;
}

export interface SwipeCardProps {
  item: RecommendationItem;
  depth: number;
  enterFrom: ExitDirection | null;
  disabled?: boolean;
  onDecide: (action: Decision, direction: ExitDirection) => void;
  /** The card body (DeckCardContent). */
  children: ReactNode;
  ariaLabel: string;
}

const COMMIT_X = 140;
const COMMIT_Y = 130;
const COMMIT_VELOCITY = 650;
const EXIT = { duration: 0.32, ease: [0.4, 0, 1, 1] as const };

function offscreen(direction: ExitDirection): { x: number; y: number } {
  const width = typeof window === "undefined" ? 1200 : window.innerWidth;
  const height = typeof window === "undefined" ? 900 : window.innerHeight;
  switch (direction) {
    case "right":
      return { x: width + 200, y: 40 };
    case "left":
      return { x: -(width + 200), y: 40 };
    case "up":
      return { x: 0, y: -(height + 200) };
    default:
      return { x: 0, y: 0 };
  }
}

/** The action a released drag commits to, or null to spring back. */
export function dragDecision(offset: { x: number; y: number }, velocity: { x: number; y: number }): Decision | null {
  const horizontal = Math.abs(offset.x) >= Math.abs(offset.y);
  if (horizontal) {
    if (offset.x > COMMIT_X || velocity.x > COMMIT_VELOCITY) return "relevant";
    if (offset.x < -COMMIT_X || velocity.x < -COMMIT_VELOCITY) return "not_relevant";
  }
  if (offset.y < -COMMIT_Y || velocity.y < -COMMIT_VELOCITY) return "save";
  if (!horizontal) {
    if (offset.x > COMMIT_X) return "relevant";
    if (offset.x < -COMMIT_X) return "not_relevant";
  }
  return null;
}

function Stamp({ label, color, opacity, className }: { label: string; color: string; opacity: MotionValue<number>; className: string }) {
  return (
    <motion.span
      aria-hidden="true"
      style={{ opacity, color, borderColor: color }}
      className={clsx(
        "pointer-events-none absolute z-20 rounded-[10px] border-[3px] px-3 py-1 text-[22px] font-black tracking-[0.12em] uppercase backdrop-blur-[2px]",
        className,
      )}
    >
      {label}
    </motion.span>
  );
}

export const SwipeCard = forwardRef<SwipeCardHandle, SwipeCardProps>(function SwipeCard(
  { item, depth, enterFrom, disabled = false, onDecide, children, ariaLabel },
  ref,
) {
  const { reduced } = useMotionPreference();
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const opacity = useMotionValue(1);
  const rotate = useTransform(x, [-1200, -252, 0, 252, 1200], [-24, -14, 0, 14, 24]);
  const relevantOpacity = useTransform(x, [30, COMMIT_X], [0, 1]);
  const notRelevantOpacity = useTransform(x, [-COMMIT_X, -30], [1, 0]);
  const saveOpacity = useTransform(y, [-COMMIT_Y, -30], [1, 0]);
  const leaving = useRef(false);
  const article = useRef<HTMLElement>(null);
  const top = depth === 0;

  // Undo: fly back in from where the card left.
  useLayoutEffect(() => {
    if (!enterFrom || reduced) return;
    const start = offscreen(enterFrom);
    x.set(start.x);
    y.set(start.y);
    void animate(x, 0, SPRING_GENTLE);
    void animate(y, 0, SPRING_GENTLE);
    // Mount only: a returning card is a fresh component.
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const commit = (action: Decision) => {
    if (leaving.current || disabled) return;
    leaving.current = true;
    const direction = ACTION_EXIT_DIRECTION[action];
    const done = () => onDecide(action, direction);
    if (reduced) {
      void animate(opacity, 0, { duration: 0.15 }).then(done);
      return;
    }
    const target = offscreen(direction);
    void Promise.all([
      animate(x, target.x, EXIT),
      animate(y, target.y, EXIT),
      animate(opacity, 0, { ...EXIT, duration: 0.3 }),
    ]).then(done);
  };

  useImperativeHandle(ref, () => ({ commit, focus: () => article.current?.focus({ preventScroll: true }) }));

  const onDragEnd = (_event: unknown, info: PanInfo) => {
    const action = dragDecision(info.offset, info.velocity);
    if (action) {
      commit(action);
      return;
    }
    void animate(x, 0, SPRING);
    void animate(y, 0, SPRING);
  };

  return (
    <motion.div
      className="absolute inset-0"
      style={{ zIndex: 10 - depth }}
      initial={enterFrom ? false : { opacity: 0, scale: 0.88, y: 36 }}
      animate={{ opacity: depth === 0 ? 1 : depth === 1 ? 0.9 : 0.7, scale: 1 - depth * 0.04, y: depth * 12 }}
      exit={{ opacity: 0, transition: { duration: 0.12 } }}
      transition={SPRING}
    >
      <motion.article
        ref={article}
        tabIndex={top ? -1 : undefined}
        aria-hidden={top ? undefined : true}
        aria-label={top ? ariaLabel : undefined}
        id={`paper-card-${item.paper.id}`}
        drag={top && !disabled && !reduced}
        dragMomentum={false}
        dragElastic={0.9}
        onDragEnd={onDragEnd}
        whileDrag={{ scale: 1.02, cursor: "grabbing" }}
        style={{ x, y, rotate, opacity }}
        className={clsx(
          "relative flex h-full flex-col overflow-hidden rounded-card border border-border bg-surface surface-sheen outline-none",
          top ? "cursor-grab elevation-raised focus-visible:ring-2 focus-visible:ring-accent/45" : "pointer-events-none elevation-card",
          "touch-pan-y select-none",
        )}
      >
        {top ? (
          <>
            <motion.span
              aria-hidden="true"
              style={{ opacity: relevantOpacity, boxShadow: `inset 0 0 0 2px ${STATE_META.relevant.color}, 0 0 48px -12px ${STATE_META.relevant.color}` }}
              className="pointer-events-none absolute inset-0 z-10 rounded-card"
            />
            <motion.span
              aria-hidden="true"
              style={{ opacity: notRelevantOpacity, boxShadow: `inset 0 0 0 2px ${STATE_META.not_relevant.color}, 0 0 48px -12px ${STATE_META.not_relevant.color}` }}
              className="pointer-events-none absolute inset-0 z-10 rounded-card"
            />
            <motion.span
              aria-hidden="true"
              style={{ opacity: saveOpacity, boxShadow: `inset 0 0 0 2px ${STATE_META.saved.color}, 0 0 48px -12px ${STATE_META.saved.color}` }}
              className="pointer-events-none absolute inset-0 z-10 rounded-card"
            />
            <Stamp label="Relevant" color={STATE_META.relevant.color} opacity={relevantOpacity} className="top-7 left-6 -rotate-12" />
            <Stamp label="Not relevant" color={STATE_META.not_relevant.color} opacity={notRelevantOpacity} className="top-7 right-6 rotate-12" />
            <Stamp label="Save" color={STATE_META.saved.color} opacity={saveOpacity} className="bottom-24 left-1/2 -translate-x-1/2" />
          </>
        ) : null}
        {children}
      </motion.article>
    </motion.div>
  );
});
