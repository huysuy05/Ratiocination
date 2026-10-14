/**
 * Full-bleed marquee of arXiv category tiles sliding left at ~100 px/s (CSS, landing.css). The set
 * is rendered twice for a seamless loop; the copy is aria-hidden. It pauses on hover, while focused,
 * off screen, or with the pause button; under reduced motion it stands still (and the button hides).
 */
import clsx from "clsx";
import { Pause, Play } from "lucide-react";
import { useRef, useState } from "react";
import { useMotionPreference } from "../../lib/motion";
import { CATEGORY_TILES, type CategoryTile, type TileArt } from "./content";
import { useOnScreen } from "./lib/hooks";

const TILE_TONE: Record<TileArt, string> = {
  rings: "bg-(--brand-accent-deep) text-(--brand-white)",
  quote: "bg-(--lp-ink) text-(--lp-on-ink) ring-1 ring-(--lp-ink-border) ring-inset",
  graph: "bg-(--lp-panel) text-(--lp-text)",
  bars: "bg-(--lp-panel-soft) text-(--lp-text)",
  disruption: "bg-(--lp-panel-paper) text-(--lp-text)",
};

function TileArtwork({ art }: { art: TileArt }) {
  switch (art) {
    case "rings":
      return (
        <svg aria-hidden="true" viewBox="0 0 300 300" className="absolute inset-0 size-full">
          {[70, 130, 190, 250].map((r) => (
            <circle key={r} cx="300" cy="300" r={r} fill="none" stroke="rgba(255,255,255,0.42)" strokeWidth="1.5" />
          ))}
          {[
            [300 - 130 * Math.cos(0.5), 300 - 130 * Math.sin(0.5), 5],
            [300 - 190 * Math.cos(1.05), 300 - 190 * Math.sin(1.05), 4],
            [300 - 250 * Math.cos(0.3), 300 - 250 * Math.sin(0.3), 3.5],
          ].map(([cx, cy, r]) => (
            <g key={cx}>
              <circle cx={cx} cy={cy} r={(r ?? 4) * 2.6} fill="rgba(255,255,255,0.16)" />
              <circle cx={cx} cy={cy} r={r} fill="#ffffff" />
            </g>
          ))}
        </svg>
      );
    case "quote":
      return (
        <svg aria-hidden="true" viewBox="0 0 300 300" className="absolute inset-0 size-full">
          <text x="244" y="318" textAnchor="end" className="font-serif" fontSize="250" fontWeight="700" fill="var(--lp-on-ink-muted)">
            “
          </text>
        </svg>
      );
    case "graph": {
      const nodes = [
        [112, 190],
        [204, 150],
        [262, 186],
        [226, 254],
        [150, 250],
      ] as const;
      const edges = [
        [0, 1],
        [1, 2],
        [1, 3],
        [2, 3],
        [3, 4],
        [4, 0],
      ] as const;
      return (
        <svg aria-hidden="true" viewBox="0 0 300 300" className="absolute inset-0 size-full">
          {edges.map(([from, to]) => (
            <line
              key={`${from}-${to}`}
              x1={nodes[from][0]}
              y1={nodes[from][1]}
              x2={nodes[to][0]}
              y2={nodes[to][1]}
              stroke="currentColor"
              strokeWidth="1.6"
            />
          ))}
          {nodes.map(([cx, cy], index) =>
            index === 1 ? (
              <circle key={index} cx={cx} cy={cy} r="11" fill="var(--brand-accent-soft)" />
            ) : (
              <circle key={index} cx={cx} cy={cy} r="7" fill="var(--lp-panel)" stroke="currentColor" strokeWidth="1.6" />
            ),
          )}
        </svg>
      );
    }
    case "bars": {
      const heights = [52, 78, 62, 122, 96];
      return (
        <svg aria-hidden="true" viewBox="0 0 300 300" className="absolute inset-0 size-full">
          {heights.map((height, index) => (
            <rect
              key={index}
              x={148 + index * 26}
              y={268 - height}
              width="18"
              height={height}
              rx="3"
              fill={index === 3 ? "var(--lp-save)" : "currentColor"}
            />
          ))}
        </svg>
      );
    }
    case "disruption":
      return (
        <div aria-hidden="true" className="absolute bottom-[14%] left-[9.5%] flex flex-col items-start gap-2.5">
          <span className="font-mono text-[24px] md:text-[30px] leading-none font-semibold tracking-[-0.02em]">CD +0.42</span>
          <span className="rounded-full bg-(--lp-button) px-2.5 py-1 text-[12px] leading-none font-semibold text-(--lp-on-button)">Disruptive</span>
        </div>
      );
  }
}

function Tile({ tile }: { tile: CategoryTile }) {
  return (
    <div className={clsx("lp-tile relative overflow-hidden rounded-[24px] p-6 md:rounded-[30px] md:p-7", TILE_TONE[tile.art])}>
      <TileArtwork art={tile.art} />
      <p className="relative font-serif text-[34px] leading-none font-medium tracking-[-0.01em] md:text-[44px]">{tile.code}</p>
      <p className="relative mt-2 text-[13px] font-medium md:text-[14px]">{tile.name}</p>
    </div>
  );
}

export function CategoryMarquee() {
  const { reduced } = useMotionPreference();
  const sectionRef = useRef<HTMLElement>(null);
  const onScreen = useOnScreen(sectionRef);
  // null until the visitor uses the button (then focusing it no longer pauses on its own).
  const [paused, setPaused] = useState<boolean | null>(null);
  const tiles = [...CATEGORY_TILES, ...CATEGORY_TILES];

  return (
    <section
      ref={sectionRef}
      aria-labelledby="categories-title"
      data-offscreen={onScreen ? undefined : "true"}
      data-paused={paused === null ? undefined : String(paused)}
      className="lp-marquee relative"
    >
      <h2 id="categories-title" className="sr-only">
        arXiv categories
      </h2>
      <div className="overflow-hidden">
        <ul className="lp-marquee-track lp-loop">
          {tiles.map((tile, index) => (
            <li key={`${tile.code}-${index}`} aria-hidden={index >= CATEGORY_TILES.length ? true : undefined} className="shrink-0">
              <Tile tile={tile} />
            </li>
          ))}
        </ul>
      </div>
      {reduced ? null : (
        <div className="mx-auto flex w-full max-w-[1440px] justify-end px-4 pt-3 sm:px-6 lg:px-10">
          <button
            type="button"
            onClick={() => setPaused((value) => !value)}
            aria-label={paused ? "Play the moving categories" : "Pause the moving categories"}
            className="touch-target relative grid size-8 place-items-center rounded-full border border-(--lp-hairline) text-(--lp-muted) transition-colors hover:border-(--lp-muted) hover:text-(--lp-text)"
          >
            {paused ? <Play aria-hidden="true" className="size-3.5" /> : <Pause aria-hidden="true" className="size-3.5" />}
          </button>
        </div>
      )}
    </section>
  );
}
