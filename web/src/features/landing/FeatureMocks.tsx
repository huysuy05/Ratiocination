/**
 * The three feature mocks (decorative, aria-hidden), each drawn at 559 × 504 design pixels and
 * scaled to its column. They animate when scrolled into view and rest on their final frame under
 * reduced motion: the swipe card sways right with its stamp while the → button pulses; the score
 * bar's segments grow in sequence; the graph's edges draw out from the focus paper, its nodes pop
 * in and the focus node pulses.
 */
import clsx from "clsx";
import { ArrowLeft, ArrowRight, ArrowUp, Check, RotateCcw, Sparkles, type LucideIcon } from "lucide-react";
import { motion, useInView, type Transition } from "motion/react";
import { useRef, type ReactNode } from "react";
import { useMotionPreference } from "../../lib/motion";
import { HERO_PAPERS } from "./content";
import { Chip } from "./DemoPaperCard";
import { useFitScale } from "./lib/hooks";

const DESIGN_WIDTH = 559;
const DESIGN_HEIGHT = 504;
const EASE_OUT = [0.22, 1, 0.36, 1] as const;

/** `once`: stay "in view" after the first time (draw-in mocks); otherwise loops stop off screen. */
function MockPanel({ className, once = false, children }: { className: string; once?: boolean; children: (inView: boolean) => ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useFitScale(ref, DESIGN_WIDTH);
  const inView = useInView(ref, { amount: 0.35, once });
  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={clsx("relative aspect-[559/504] w-full max-w-[559px] overflow-hidden rounded-[24px] sm:rounded-[30px]", className)}
    >
      <div className="absolute top-0 left-0 origin-top-left [scale:var(--fit,1)]" style={{ width: DESIGN_WIDTH, height: DESIGN_HEIGHT }}>
        {children(inView)}
      </div>
    </div>
  );
}

// ---- 1. Swipe a daily deck ------------------------------------------------------------------

const SWAY: Transition = { duration: 3.6, times: [0, 0.32, 0.64, 1], ease: "easeInOut", repeat: Infinity, repeatDelay: 1 };

/** A deck action button; `pulsing` (true or false) adds the pulse ring, which runs while true. */
function DeckButton({ icon: Icon, tone, pulsing }: { icon: LucideIcon; tone: string; pulsing?: boolean }) {
  return (
    <span className="relative grid size-[60px] place-items-center rounded-full border-2 bg-(--lp-card)" style={{ borderColor: tone, color: tone }}>
      {pulsing === undefined ? null : (
        <motion.span
          className="absolute -inset-[2px] rounded-full border-2"
          style={{ borderColor: tone }}
          initial={{ opacity: 0, scale: 1 }}
          animate={pulsing ? { opacity: [0, 0.6, 0, 0], scale: [1, 1, 1.45, 1.45] } : { opacity: 0, scale: 1 }}
          transition={pulsing ? SWAY : { duration: 0 }}
        />
      )}
      <Icon aria-hidden="true" className="size-[22px]" strokeWidth={2.2} />
    </span>
  );
}

export function SwipeMock() {
  const { reduced } = useMotionPreference();
  const paper = HERO_PAPERS[1];
  return (
    <MockPanel className="bg-(--lp-panel)">
      {(inView) => {
        const active = inView && !reduced;
        return (
          <>
            <motion.div
              className="absolute top-[100px] left-[100px] w-[360px]"
              initial={false}
              animate={active ? { x: [0, 34, 34, 0], rotate: [5, 9.5, 9.5, 5] } : { x: 0, rotate: 5 }}
              transition={active ? SWAY : { duration: 0.4, ease: EASE_OUT }}
            >
              <div className="rounded-[18px] border border-(--lp-card-border) bg-(--lp-card) p-5 text-(--lp-text) shadow-(--lp-card-shadow)">
                <div className="flex gap-2">
                  <Chip accent>{paper.category}</Chip>
                  <Chip>Open access</Chip>
                  <Chip>Cited by {paper.citedBy}</Chip>
                </div>
                <p className="mt-3 font-serif text-[20px] leading-[26px] font-semibold tracking-[-0.01em]">{paper.title}</p>
                <p className="mt-2 text-[12.5px] text-(--lp-muted)">M. Okafor, L. Chen, A. Diaz et al. · 2026</p>
              </div>
              <motion.span
                className="absolute -top-[30px] left-[24px] -rotate-[17deg] rounded-[8px] border-[3px] border-(--lp-relevant) bg-(--lp-card) px-2.5 text-[19px] leading-[28px] font-extrabold tracking-[0.08em] text-(--lp-relevant) uppercase"
                initial={false}
                animate={active ? { opacity: [0.3, 1, 1, 0.3] } : { opacity: reduced ? 1 : 0.3 }}
                transition={active ? SWAY : { duration: 0.3 }}
              >
                Relevant
              </motion.span>
            </motion.div>
            <div className="absolute top-[388px] left-1/2 flex -translate-x-1/2 gap-[18px]">
              <DeckButton icon={ArrowLeft} tone="var(--lp-not-relevant)" />
              <DeckButton icon={RotateCcw} tone="var(--lp-quiet)" />
              <DeckButton icon={ArrowUp} tone="var(--lp-save)" />
              <DeckButton icon={ArrowRight} tone="var(--lp-relevant)" pulsing={active} />
            </div>
          </>
        );
      }}
    </MockPanel>
  );
}

// ---- 2. Know why every paper ------------------------------------------------------------------

const REASONS: readonly { icon: LucideIcon; color: string; title: string; detail: string }[] = [
  { icon: Check, color: "var(--brand-feat-question)", title: "Cites your seed paper", detail: "“Switch Transformers” (Fedus et al., 2022)" },
  { icon: Sparkles, color: "var(--brand-feat-draft)", title: "Matches §3 of your draft", detail: "routing-stability.pdf · page 4 · “load balancing…”" },
  { icon: Check, color: "var(--brand-feat-anchor)", title: "In a category you follow", detail: "cs.LG · posted 2 days ago" },
];

const SEGMENTS: readonly { label: string; share: number; color: string }[] = [
  { label: "Question", share: 30, color: "var(--brand-feat-question)" },
  { label: "Anchor", share: 24, color: "var(--brand-feat-anchor)" },
  { label: "Draft", share: 18, color: "var(--brand-feat-draft)" },
  { label: "Citation", share: 12, color: "var(--brand-feat-citation)" },
  { label: "Preference", share: 10, color: "var(--brand-feat-preference)" },
  { label: "Recency", share: 6, color: "var(--brand-feat-recency)" },
];

export function WhyMock() {
  const { reduced } = useMotionPreference();
  return (
    <MockPanel once className="bg-(--lp-ink) ring-1 ring-(--lp-ink-border) ring-inset">
      {(inView) => {
        const grown = inView || reduced;
        return (
          <div className="grid size-full place-items-center">
            <div className="w-[459px] rounded-[16px] bg-(--lp-card) p-6 text-(--lp-text) shadow-[0_30px_60px_-30px_rgba(0,0,0,0.6)]">
              <p className="text-[17px] font-bold">Why this paper</p>
              <ul className="mt-4 flex flex-col gap-3.5">
                {REASONS.map(({ icon: Icon, color, title, detail }) => (
                  <li key={title} className="flex items-start gap-3">
                    <span className="mt-0.5 grid size-[26px] shrink-0 place-items-center rounded-full text-white" style={{ backgroundColor: color }}>
                      <Icon aria-hidden="true" className="size-3.5" strokeWidth={2.6} />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-[14px] leading-5 font-semibold">{title}</span>
                      <span className="block truncate text-[12.5px] leading-5 text-(--lp-muted)">{detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-5 text-[10.5px] font-bold tracking-[0.1em] text-(--lp-muted) uppercase">Score breakdown</p>
              <div className="mt-2 flex h-3 gap-[2px] overflow-hidden rounded-full bg-(--lp-chip)">
                {SEGMENTS.map((segment, index) => (
                  <motion.span
                    key={segment.label}
                    className="h-full origin-left first:rounded-l-full last:rounded-r-full"
                    style={{ width: `${segment.share}%`, backgroundColor: segment.color }}
                    initial={false}
                    animate={{ scaleX: grown ? 1 : 0 }}
                    transition={reduced ? { duration: 0 } : { duration: 0.45, delay: 0.25 + index * 0.17, ease: EASE_OUT }}
                  />
                ))}
              </div>
              <ul className="mt-3 flex flex-wrap gap-x-3.5 gap-y-1 text-[11.5px] text-(--lp-muted)">
                {SEGMENTS.map((segment) => (
                  <li key={segment.label} className="flex items-center gap-1.5">
                    <span className="size-2 rounded-full" style={{ backgroundColor: segment.color }} />
                    {segment.label}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        );
      }}
    </MockPanel>
  );
}

// ---- 3. Map the breakthroughs -----------------------------------------------------------------

type Relation = "cites" | "similar" | "topic" | "authored";
type NodeKind = "relevant" | "saved" | "unrated" | "topic" | "author";

const CENTER = { x: 279, y: 210 };

const NEIGHBOURS: readonly { label: string; angle: number; radius: number; relation: Relation; kind: NodeKind }[] = [
  { label: "Fedus ’22", angle: 0, radius: 134, relation: "cites", kind: "relevant" },
  { label: "Shazeer ’17", angle: 45, radius: 150, relation: "cites", kind: "unrated" },
  { label: "MoE routing", angle: 90, radius: 150, relation: "topic", kind: "topic" },
  { label: "Chen ’24", angle: 135, radius: 150, relation: "similar", kind: "saved" },
  { label: "Okafor ’26", angle: 180, radius: 130, relation: "authored", kind: "author" },
  { label: "Expert collapse", angle: 225, radius: 150, relation: "topic", kind: "topic" },
  { label: "Diaz ’25", angle: 270, radius: 150, relation: "similar", kind: "unrated" },
  { label: "Lepikhin ’20", angle: 315, radius: 150, relation: "cites", kind: "unrated" },
];

const EDGE_STYLE: Record<Relation, { stroke: string; width: number; dash?: string }> = {
  cites: { stroke: "var(--lp-edge-cites)", width: 1.8 },
  similar: { stroke: "var(--lp-edge-similar)", width: 1.6, dash: "6 5" },
  topic: { stroke: "var(--lp-edge-topic)", width: 2, dash: "0.5 5" },
  authored: { stroke: "var(--lp-edge-authored)", width: 1 },
};

function polar(angle: number, radius: number) {
  const radians = (angle * Math.PI) / 180;
  return { x: CENTER.x + radius * Math.sin(radians), y: CENTER.y - radius * Math.cos(radians) };
}

function NodeShape({ kind }: { kind: NodeKind }) {
  switch (kind) {
    case "relevant":
      return <circle r="8" fill="var(--lp-relevant)" />;
    case "saved":
      return <circle r="8" fill="var(--lp-save)" />;
    case "unrated":
      return <circle r="7.5" fill="var(--lp-panel-paper)" stroke="var(--lp-text)" strokeWidth="1.6" />;
    case "topic":
      return <rect x="-7.5" y="-7.5" width="15" height="15" rx="3.5" fill="var(--lp-panel-paper)" stroke="var(--lp-edge-topic)" strokeWidth="1.8" />;
    case "author":
      return <rect x="-6.5" y="-6.5" width="13" height="13" rx="1.5" transform="rotate(45)" fill="var(--lp-author)" />;
  }
}

function LegendLine({ relation, label }: { relation: Relation; label: string }) {
  const style = EDGE_STYLE[relation];
  return (
    <span className="flex items-center gap-2">
      <svg width="26" height="6" viewBox="0 0 26 6" aria-hidden="true">
        <line x1="1" y1="3" x2="25" y2="3" stroke={style.stroke} strokeWidth={style.width + 0.2} strokeDasharray={style.dash} strokeLinecap="round" />
      </svg>
      {label}
    </span>
  );
}

export function GraphMock() {
  const { reduced } = useMotionPreference();
  return (
    <MockPanel once className="bg-(--lp-panel-paper)">
      {(inView) => {
        const shown = inView || reduced;
        const instant = { duration: 0 };
        return (
          <>
            <svg viewBox={`0 0 ${DESIGN_WIDTH} ${DESIGN_HEIGHT}`} className="absolute inset-0 size-full overflow-visible">
              {NEIGHBOURS.map((node, index) => {
                const end = polar(node.angle, node.radius - 11);
                const style = EDGE_STYLE[node.relation];
                return (
                  <motion.line
                    key={node.label}
                    x1={CENTER.x}
                    y1={CENTER.y}
                    initial={false}
                    animate={shown ? { x2: end.x, y2: end.y } : { x2: CENTER.x, y2: CENTER.y }}
                    transition={reduced ? instant : { duration: 0.55, delay: 0.1 + index * 0.07, ease: EASE_OUT }}
                    stroke={style.stroke}
                    strokeWidth={style.width}
                    strokeDasharray={style.dash}
                    strokeLinecap="round"
                  />
                );
              })}
              {NEIGHBOURS.map((node, index) => {
                const at = polar(node.angle, node.radius);
                const sin = Math.sin((node.angle * Math.PI) / 180);
                const cos = Math.cos((node.angle * Math.PI) / 180);
                const anchor = sin > 0.3 ? "start" : sin < -0.3 ? "end" : "middle";
                const label = { x: at.x + sin * 17, y: at.y - cos * 19 + (Math.abs(sin) > 0.3 ? 4 : cos > 0 ? 0 : 8) };
                return (
                  <motion.g
                    key={node.label}
                    initial={false}
                    animate={shown ? { opacity: 1 } : { opacity: 0 }}
                    transition={reduced ? instant : { duration: 0.35, delay: 0.45 + index * 0.07 }}
                  >
                    <motion.g
                      style={{ x: at.x, y: at.y }}
                      initial={false}
                      animate={{ scale: shown ? 1 : 0.2 }}
                      transition={reduced ? instant : { type: "spring", stiffness: 420, damping: 22, delay: 0.45 + index * 0.07 }}
                    >
                      <NodeShape kind={node.kind} />
                    </motion.g>
                    <text x={label.x} y={label.y} textAnchor={anchor} fontSize="12.5" fontWeight="500" fill="var(--lp-text)">
                      {node.label}
                    </text>
                  </motion.g>
                );
              })}
              <circle cx={CENTER.x} cy={CENTER.y} r="22" fill="var(--lp-text)" opacity="0.12" />
              {shown && !reduced ? (
                <motion.circle
                  cx={CENTER.x}
                  cy={CENTER.y}
                  fill="none"
                  stroke="var(--lp-text)"
                  strokeWidth="1.5"
                  initial={{ r: 13, opacity: 0.6 }}
                  animate={{ r: 34, opacity: 0 }}
                  transition={{ duration: 2.2, ease: "easeOut", repeat: Infinity, repeatDelay: 0.6, delay: 1.2 }}
                />
              ) : null}
              <circle cx={CENTER.x} cy={CENTER.y} r="13" fill="var(--lp-text)" />
            </svg>
            <div className="absolute inset-x-9 bottom-8 flex items-center justify-between text-[12px] text-(--lp-muted)">
              <div className="flex items-center gap-5">
                <LegendLine relation="cites" label="Cites" />
                <LegendLine relation="similar" label="Similar" />
                <LegendLine relation="topic" label="Topic" />
              </div>
              <span className="rounded-full bg-(--lp-button) px-3 py-1.5 font-mono text-[11.5px] font-semibold text-(--lp-on-button)">CD +0.42 · Disruptive</span>
            </div>
          </>
        );
      }}
    </MockPanel>
  );
}
