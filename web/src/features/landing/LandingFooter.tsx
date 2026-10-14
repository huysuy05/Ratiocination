/**
 * Footer card: concentric rings ripple outward from the white R mark (SVG circles animating `r`,
 * landing.css; evenly spaced and still under reduced motion), with "Private by default", the
 * copyright and a GitHub link along the bottom.
 */
import { useRef } from "react";
import { LANDING_ASSETS, REPO_URL } from "./content";
import { useOnScreen } from "./lib/hooks";

const RINGS = 7;
const RIPPLE_SECONDS = 14;
const MAX_RADIUS = 760;

export function LandingFooter() {
  const ref = useRef<HTMLElement>(null);
  const onScreen = useOnScreen(ref);
  return (
    <footer ref={ref} data-offscreen={onScreen ? undefined : "true"} className="mx-auto w-full max-w-[1440px] px-4 pb-4 sm:px-6 sm:pb-6 lg:px-10 lg:pb-10">
      <div className="relative isolate flex h-[400px] flex-col justify-end overflow-hidden rounded-[24px] bg-(--lp-ink) text-(--lp-on-ink) ring-1 ring-(--lp-ink-border) ring-inset sm:rounded-[30px] lg:h-[460px]">
        <svg
          aria-hidden="true"
          width={MAX_RADIUS * 2}
          height={MAX_RADIUS * 2}
          viewBox={`0 0 ${MAX_RADIUS * 2} ${MAX_RADIUS * 2}`}
          className="pointer-events-none absolute top-[46%] left-1/2 -z-10 max-w-none -translate-x-1/2 -translate-y-1/2"
        >
          {Array.from({ length: RINGS }, (_, index) => (
            <circle
              key={index}
              className="lp-ripple lp-loop"
              cx={MAX_RADIUS}
              cy={MAX_RADIUS}
              r={((index + 1) / RINGS) * MAX_RADIUS * 0.82}
              fill="none"
              stroke="var(--lp-ring)"
              strokeWidth="1.2"
              style={{ animationDelay: `${(-(index * RIPPLE_SECONDS) / RINGS).toFixed(2)}s` }}
            />
          ))}
        </svg>
        <img
          src={LANDING_ASSETS.markWhite}
          alt=""
          width={LANDING_ASSETS.markSize.width}
          height={LANDING_ASSETS.markSize.height}
          loading="lazy"
          className="absolute top-[46%] left-1/2 w-[112px] -translate-x-1/2 -translate-y-1/2 sm:w-[140px] lg:w-[176px]"
        />
        <div className="relative flex flex-col items-center px-6 pb-5 text-[15px] sm:grid sm:grid-cols-3 sm:items-end sm:gap-4 sm:px-8 sm:pb-6 lg:px-10 lg:pb-7 lg:text-[16px]">
          <p className="flex min-h-11 items-center font-medium sm:justify-self-start">Private by default</p>
          <p className="flex min-h-11 items-center text-(--lp-on-ink-muted) sm:justify-self-center">© 2026 Ratiocination</p>
          <a
            href={REPO_URL}
            rel="noopener noreferrer"
            className="inline-flex min-h-11 items-center rounded-[4px] px-1 font-medium underline-offset-4 hover:underline focus-visible:outline-(--lp-on-ink) sm:justify-self-end"
          >
            Source code (AGPL)
          </a>
        </div>
      </div>
    </footer>
  );
}
