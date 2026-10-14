/**
 * The navy statement card: the dictionary entry and a radar scope whose sweep turns once per 8 s
 * while four blips flare as it passes (CSS, landing.css; paused off screen; still under reduced
 * motion).
 */
import { useRef } from "react";
import { useOnScreen } from "./lib/hooks";

const SWEEP_SECONDS = 8;
/** Blips: angle clockwise from 12 o'clock (deg) and distance from the centre (% of the radius). */
const BLIPS = [
  { angle: 38, distance: 0.62 },
  { angle: 112, distance: 0.36 },
  { angle: 205, distance: 0.78 },
  { angle: 292, distance: 0.5 },
] as const;

function Radar() {
  return (
    <div aria-hidden="true" className="relative aspect-square w-full">
      <svg viewBox="0 0 520 520" className="absolute inset-0 size-full">
        {[258, 194, 130, 66].map((r) => (
          <circle key={r} cx="260" cy="260" r={r} fill="none" stroke="var(--lp-ring)" strokeWidth="1.2" />
        ))}
        <line x1="2" y1="260" x2="518" y2="260" stroke="var(--lp-ring)" strokeWidth="1" />
        <line x1="260" y1="2" x2="260" y2="518" stroke="var(--lp-ring)" strokeWidth="1" />
      </svg>
      <div className="lp-sweep lp-loop absolute inset-[0.5%]" />
      {BLIPS.map(({ angle, distance }) => {
        const radians = (angle * Math.PI) / 180;
        const left = 50 + 49.6 * distance * Math.sin(radians);
        const top = 50 - 49.6 * distance * Math.cos(radians);
        // Negative delay: the flare (keyframe 0 %) lands when the sweep reaches the blip's angle.
        const delay = (angle / 360) * SWEEP_SECONDS - SWEEP_SECONDS;
        return (
          <span
            key={angle}
            className="lp-blip lp-loop absolute size-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-(--brand-accent-soft) shadow-[0_0_16px_5px_rgba(216,207,188,0.5)]"
            style={{ left: `${left}%`, top: `${top}%`, animationDelay: `${delay.toFixed(2)}s` }}
          />
        );
      })}
    </div>
  );
}

export function StatementCard() {
  const ref = useRef<HTMLElement>(null);
  const onScreen = useOnScreen(ref);
  return (
    <section ref={ref} aria-labelledby="statement-term" data-offscreen={onScreen ? undefined : "true"} className="mx-auto w-full max-w-[1440px] px-4 sm:px-6 lg:px-10">
      <div className="relative isolate grid overflow-hidden rounded-[24px] bg-(--lp-ink) text-(--lp-on-ink) ring-1 ring-(--lp-ink-border) ring-inset sm:rounded-[30px] lg:h-[560px] lg:grid-cols-[minmax(0,1fr)_minmax(0,600px)] lg:items-center">
        <div className="relative z-10 px-6 pt-10 sm:px-10 sm:pt-14 lg:py-0 lg:pr-0 lg:pl-16">
          <h2 id="statement-term" className="lp-eyebrow text-(--lp-on-ink-muted)">
            <span aria-hidden="true">ra·ti·oc·i·na·tion (n.)</span>
            <span className="sr-only">Ratiocination, noun</span>
          </h2>
          <p className="mt-5 max-w-[600px] font-serif text-[34px] leading-[1.14] tracking-[-0.01em] text-balance sm:text-[46px] lg:text-[56px] xl:text-[60px] xl:leading-[1.1]">
            The process of exact thinking. A reasoned path through what’s new.
          </p>
        </div>
        <div className="relative mx-auto -mb-[18%] w-[min(92%,440px)] pt-8 sm:w-[min(80%,480px)] lg:m-0 lg:w-[520px] lg:justify-self-center lg:pt-0">
          <Radar />
        </div>
      </div>
    </section>
  );
}
