/**
 * A paper card as the feed shows it, drawn for the landing mocks (decorative: the caller hides it
 * from assistive tech). `tone="hero"` uses the theme-invariant brand colours (the hero card is a
 * light surface in both themes); `tone="theme"` follows the landing tokens.
 */
import clsx from "clsx";
import { Check, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import type { DemoPaper } from "./content";

export function Chip({ children, accent = false, hero = false }: { children: ReactNode; accent?: boolean; hero?: boolean }) {
  return (
    <span
      className={clsx(
        "inline-flex h-[22px] items-center rounded-full px-2.5 font-mono text-[11.5px] leading-none whitespace-nowrap",
        hero
          ? accent
            ? "bg-(--brand-accent-soft) font-semibold text-(--brand-chip-ink)"
            : "bg-(--brand-tint) text-(--brand-muted)"
          : accent
            ? "bg-(--lp-cat-chip) font-semibold text-(--lp-cat-chip-text)"
            : "bg-(--lp-chip) text-(--lp-chip-text)",
      )}
    >
      {children}
    </span>
  );
}

export function ReasonPill({ kind, text, hero = false }: { kind: "metadata" | "model"; text: string; hero?: boolean }) {
  const Icon = kind === "metadata" ? Check : Sparkles;
  return (
    <span
      className={clsx(
        "inline-flex h-[26px] items-center gap-1.5 rounded-full border px-2.5 text-[12px] font-medium whitespace-nowrap",
        hero
          ? "border-(--brand-hairline) bg-(--brand-white) text-(--brand-ink)"
          : "border-(--lp-card-border) bg-(--lp-card) text-(--lp-text)",
      )}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" strokeWidth={2.4} />
      {text}
    </span>
  );
}

/** The hero deck card: 460 × 300 design pixels (scaled by the deck). */
export function HeroPaperCard({ paper, stamp }: { paper: DemoPaper; stamp?: ReactNode }) {
  return (
    <div className="relative flex h-[300px] w-[460px] flex-col rounded-[20px] bg-(--brand-white) p-6 text-(--brand-ink) shadow-(--brand-card-shadow)">
      <div className="flex items-center gap-2">
        <Chip hero accent>
          {paper.category}
        </Chip>
        <Chip hero>{paper.age}</Chip>
        <Chip hero>Cited by {paper.citedBy}</Chip>
      </div>
      <p className="mt-3.5 line-clamp-2 font-serif text-[21px] leading-[28px] font-semibold tracking-[-0.01em] text-balance">
        {paper.title}
      </p>
      <p className="mt-2 truncate text-[12.5px] text-(--brand-muted)">{paper.authors}</p>
      <p className="mt-2.5 line-clamp-2 text-[13px] leading-[20px] text-(--brand-muted)">{paper.abstract}</p>
      <div className="mt-auto flex gap-2">
        {paper.reasons.map((reason) => (
          <ReasonPill key={reason.text} hero kind={reason.kind} text={reason.text} />
        ))}
      </div>
      {stamp}
    </div>
  );
}
