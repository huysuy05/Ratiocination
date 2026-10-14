/**
 * The hero's demo deck (decorative, aria-hidden): three paper cards replaying a day in the feed on
 * an 8-second loop (lib/deckTimeline.ts). One requestAnimationFrame clock drives every card through
 * Motion values (no React renders per frame); it only runs while `playing` (on screen, tab visible,
 * full motion). Under reduced motion the deck rests at its first frame.
 */
import { motion, motionValue, type MotionValue } from "motion/react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { HERO_PAPERS } from "./content";
import { HeroPaperCard } from "./DemoPaperCard";
import { deckFrame, type CardFrame, type DeckCardId } from "./lib/deckTimeline";

type CardValues = { [K in keyof CardFrame]: MotionValue<number> };

function createCardValues(): CardValues {
  return {
    x: motionValue(0),
    y: motionValue(0),
    scale: motionValue(1),
    rotate: motionValue(0),
    opacity: motionValue(1),
    stamp: motionValue(0),
  };
}

const CARD_IDS: readonly DeckCardId[] = ["a", "b", "c"];
/** Longest step the clock takes per frame (a slow frame never makes the deck jump). */
const MAX_STEP_MS = 64;

function Stamp({ label, tone, opacity, className }: { label: string; tone: "relevant" | "save"; opacity: MotionValue<number>; className: string }) {
  return (
    <motion.span
      style={{ opacity }}
      className={
        "pointer-events-none absolute rounded-[9px] border-[3px] bg-(--brand-white)/85 px-3 py-0.5 text-[20px] leading-[28px] font-extrabold tracking-[0.08em] uppercase " +
        (tone === "relevant" ? "border-(--brand-relevant) text-(--brand-relevant) " : "border-(--brand-save) text-(--brand-save) ") +
        className
      }
    >
      {label}
    </motion.span>
  );
}

export interface HeroDeckProps {
  /** Run the clock (on screen, tab visible, full motion). */
  playing: boolean;
  /** Reduced motion: rest at the first frame. */
  reduced: boolean;
  /** How far (deck pixels) a waiting card sits below its slot: just past the hero card's bottom edge. */
  riseRef: RefObject<number>;
}

export function HeroDeck({ playing, reduced, riseRef }: HeroDeckProps) {
  const [cards] = useState(() => ({ a: createCardValues(), b: createCardValues(), c: createCardValues() }));
  const timeRef = useRef(0);

  useEffect(() => {
    const apply = (seconds: number) => {
      const frame = deckFrame(seconds, riseRef.current);
      for (const id of CARD_IDS) {
        const values = cards[id];
        const next = frame[id];
        values.x.set(next.x);
        values.y.set(next.y);
        values.scale.set(next.scale);
        values.rotate.set(next.rotate);
        values.opacity.set(next.opacity);
        values.stamp.set(next.stamp);
      }
    };
    if (reduced) timeRef.current = 0;
    apply(timeRef.current);
    if (!playing || reduced) return;
    let raf = 0;
    let last: number | null = null;
    const tick = (now: number) => {
      if (last !== null) timeRef.current += Math.min(now - last, MAX_STEP_MS) / 1000;
      last = now;
      apply(timeRef.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [cards, playing, reduced, riseRef]);

  const [paperA, paperB, paperC] = HERO_PAPERS;
  const layers: { id: DeckCardId; z: number; paper: typeof paperA; stamp?: { label: string; tone: "relevant" | "save"; className: string } }[] = [
    { id: "c", z: 1, paper: paperC },
    { id: "b", z: 2, paper: paperB, stamp: { label: "Save", tone: "save", className: "top-[104px] left-[178px]" } },
    { id: "a", z: 3, paper: paperA, stamp: { label: "Relevant", tone: "relevant", className: "top-[44px] left-[22px] -rotate-12" } },
  ];

  return (
    <div className="relative h-[324px] w-[460px]">
      {layers.map(({ id, z, paper, stamp }) => {
        const values = cards[id];
        return (
          <motion.div
            key={id}
            className="absolute top-0 left-0 will-change-transform"
            style={{ x: values.x, y: values.y, scale: values.scale, rotate: values.rotate, opacity: values.opacity, zIndex: z }}
          >
            <HeroPaperCard
              paper={paper}
              stamp={stamp ? <Stamp label={stamp.label} tone={stamp.tone} opacity={values.stamp} className={stamp.className} /> : undefined}
            />
          </motion.div>
        );
      })}
    </div>
  );
}
