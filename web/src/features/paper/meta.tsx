/**
 * Shared paper presentation (UI.md §7–8): reason pills, metadata chips and disruption formatting,
 * used by deck cards, the detail sheet, library cards and the dashboard.
 */
import clsx from "clsx";
import { BadgeCheck, CalendarDays, Eye, Quote, Sparkles, TriangleAlert, Unlock, Zap } from "lucide-react";
import type { DisruptionLabel, PaperSummary, Reason } from "../../api/types";
import { Tooltip } from "../../components/ui/Tooltip";
import { formatNumber, formatRelativeDate } from "../../lib/format";
import { categoryLabel } from "../projects/categories";

/** "+0.12" / "−0.03" (true minus sign), or "—". */
export function formatCd(cd: number | null | undefined): string {
  if (cd === null || cd === undefined || Number.isNaN(cd)) return "—";
  const abs = Math.abs(cd);
  const digits = abs > 0 && abs < 0.01 ? 4 : 2;
  const text = abs.toFixed(digits);
  return cd > 0 ? `+${text}` : cd < 0 ? `−${text}` : text;
}

/** Disruption tone from a bare CD value (summaries carry no citation split). */
export function cdLabelOf(cd: number | null | undefined): DisruptionLabel {
  if (cd === null || cd === undefined) return "undefined";
  if (cd >= 0.01) return "disruptive";
  if (cd <= -0.01) return "consolidating";
  return "mixed";
}

export const DISRUPTION_TEXT: Readonly<Record<DisruptionLabel, string>> = {
  disruptive: "Disruptive",
  consolidating: "Consolidating",
  mixed: "Mixed",
  undefined: "Not enough citations",
};

export const DISRUPTION_COLOR: Readonly<Record<DisruptionLabel, string>> = {
  disruptive: "var(--disruptive)",
  consolidating: "var(--consolidating)",
  mixed: "var(--text-muted)",
  undefined: "var(--text-faint)",
};

const CHIP = "inline-flex h-6 items-center gap-1 rounded-pill border px-2 text-[11.5px] font-medium whitespace-nowrap [&_svg]:size-3";

export function PaperChips({ paper, className }: { paper: PaperSummary; className?: string }) {
  const label = cdLabelOf(paper.cd);
  return (
    <div className={clsx("flex flex-wrap items-center gap-1.5", className)}>
      {paper.primary_category ? (
        <Tooltip content={categoryLabel(paper.primary_category)}>
          <span tabIndex={0} className={clsx(CHIP, "border-accent/30 bg-accent/10 text-accent")}>
            {paper.primary_category}
          </span>
        </Tooltip>
      ) : null}
      {paper.publication_date ? (
        <span className={clsx(CHIP, "border-border text-muted")}>
          <CalendarDays aria-hidden="true" />
          {formatRelativeDate(paper.publication_date)}
        </span>
      ) : null}
      {paper.status === "retracted" || paper.status === "withdrawn" ? (
        <span className={clsx(CHIP, "border-not-relevant/40 bg-not-relevant/12 text-not-relevant")}>
          <TriangleAlert aria-hidden="true" />
          {paper.status === "retracted" ? "Retracted" : "Withdrawn"}
        </span>
      ) : null}
      {paper.is_oa ? (
        <span className={clsx(CHIP, "border-relevant/30 text-relevant")}>
          <Unlock aria-hidden="true" />
          Open access
        </span>
      ) : null}
      {paper.cited_by_count ? (
        <span className={clsx(CHIP, "border-border text-muted")}>
          <Quote aria-hidden="true" />
          Cited by {formatNumber(paper.cited_by_count)}
        </span>
      ) : null}
      {paper.cd !== null && label !== "mixed" && label !== "undefined" ? (
        <Tooltip content={`Disruption (CD) index ${formatCd(paper.cd)}: ${label === "disruptive" ? "later work tends to cite it instead of its sources" : "later work tends to cite it together with its sources"}`}>
          <span tabIndex={0} className={clsx(CHIP)} style={{ color: DISRUPTION_COLOR[label], borderColor: `color-mix(in oklab, ${DISRUPTION_COLOR[label]} 40%, transparent)` }}>
            <Zap aria-hidden="true" />
            {DISRUPTION_TEXT[label]}
          </span>
        </Tooltip>
      ) : null}
      {paper.read_at ? (
        <span className={clsx(CHIP, "border-border text-read")}>
          <Eye aria-hidden="true" />
          Read
        </span>
      ) : null}
    </div>
  );
}

/** One "why this paper" reason as a pill; the full text shows on hover/focus. */
export function ReasonPill({ reason }: { reason: Reason }) {
  const Icon = reason.kind === "metadata" ? BadgeCheck : Sparkles;
  return (
    <Tooltip content={reason.kind === "metadata" ? `${reason.text} (from the sources)` : `${reason.text} (inferred by Ratiocination)`}>
      <span
        tabIndex={0}
        className={clsx(
          "inline-flex max-w-full items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[12.5px] leading-tight",
          reason.kind === "metadata" ? "border-relevant/25 bg-relevant/8 text-text" : "border-accent/25 bg-accent/8 text-text",
        )}
      >
        <Icon aria-hidden="true" className={clsx("size-3.5 shrink-0", reason.kind === "metadata" ? "text-relevant" : "text-accent")} />
        <span className="truncate">{reason.text}</span>
      </span>
    </Tooltip>
  );
}

/** "arXiv", "PDF", "DOI" links of a paper (external, no referrer). */
export function paperLinks(paper: PaperSummary): { label: string; href: string }[] {
  const out: { label: string; href: string }[] = [];
  if (paper.links.abs) out.push({ label: paper.identifiers.arxiv ? "arXiv" : "Abstract", href: paper.links.abs });
  if (paper.links.pdf) out.push({ label: "PDF", href: paper.links.pdf });
  if (paper.links.doi) out.push({ label: "DOI", href: paper.links.doi });
  if (paper.links.oa && !paper.links.pdf) out.push({ label: "Open access", href: paper.links.oa });
  return out;
}
