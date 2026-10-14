/**
 * The hero's scalloped "NEW · fresh deck every day · BETA" sticker. The badge shape turns once per
 * 16 s (CSS, paused off screen, still under reduced motion); the text stays upright. White on brand
 * blue is 4:1, so every line is large text (≥ 19 px bold).
 */
import clsx from "clsx";

const SIZE = 176;

/** A circle with `bumps` soft scallops around its edge. */
function scallopPath(radius: number, amplitude: number, bumps: number, steps = 288): string {
  const c = SIZE / 2;
  const points: string[] = [];
  for (let i = 0; i < steps; i += 1) {
    const angle = (i / steps) * Math.PI * 2;
    const r = radius + amplitude * Math.cos(bumps * angle);
    points.push(`${(c + r * Math.cos(angle)).toFixed(2)} ${(c + r * Math.sin(angle)).toFixed(2)}`);
  }
  return `M${points.join("L")}Z`;
}

const PATH = scallopPath(83, 4, 18);

export function Sticker({ className }: { className?: string }) {
  return (
    <div className={clsx("grid size-[148px] place-items-center sm:size-[160px] xl:size-[176px]", className)}>
      <svg aria-hidden="true" viewBox={`0 0 ${SIZE} ${SIZE}`} className="lp-sticker-spin lp-loop absolute inset-0 size-full drop-shadow-[0_10px_18px_rgba(17,18,13,0.2)]">
        <path d={PATH} fill="var(--brand-accent)" />
      </svg>
      <p className="relative text-center leading-[1.06] font-bold text-(--brand-white)">
        <span className="sr-only">New: a fresh deck every day. Beta.</span>
        <span aria-hidden="true" className="block text-[19px] tracking-[0.06em]">
          NEW
        </span>
        <span aria-hidden="true" className="block text-[22px] tracking-[-0.01em]">
          Fresh deck
        </span>
        <span aria-hidden="true" className="block text-[19px]">
          every day
        </span>
        <span aria-hidden="true" className="block text-[19px] tracking-[0.06em]">
          BETA
        </span>
      </p>
    </div>
  );
}
