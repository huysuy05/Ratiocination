/**
 * "Why this paper" (UI.md §8.2): reasons from the sources and reasons inferred by Ratiocination,
 * each expandable to the evidence behind it (anchor paper, draft passage, category, ratings…).
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { BadgeCheck, ChevronDown, FileText, Sparkles } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { AnchorKind, Reason } from "../../api/types";
import { formatScore } from "../../lib/format";
import { categoryLabel } from "../projects/categories";

const ANCHOR_KIND: Readonly<Record<AnchorKind, string>> = {
  seed: "your seed paper",
  relevant: "a paper you marked relevant",
  saved: "a paper you saved",
};

function AnchorLink({ id, title, onOpenPaper }: { id: number; title: string; onOpenPaper: (paperId: number) => void }) {
  return (
    <button type="button" onClick={() => onOpenPaper(id)} className="text-left font-serif text-accent hover:underline">
      {title}
    </button>
  );
}

function Evidence({ reason, onOpenPaper }: { reason: Reason; onOpenPaper: (paperId: number) => void }): ReactNode {
  switch (reason.code) {
    case "question":
      return <p>Cosine similarity {formatScore(reason.evidence.cosine, 2)} between this abstract and your research question.</p>;
    case "similar_to_anchor":
      return (
        <p>
          Similar ({formatScore(reason.evidence.cosine, 2)}) to {ANCHOR_KIND[reason.evidence.anchor_kind]}{" "}
          <AnchorLink id={reason.evidence.anchor_paper_id} title={reason.evidence.anchor_title} onOpenPaper={onOpenPaper} />.
        </p>
      );
    case "cites_anchor":
    case "cited_by_anchor":
      return (
        <p>
          {reason.evidence.direction === "cites" ? "Cites" : "Is cited by"} {ANCHOR_KIND[reason.evidence.anchor_kind]}{" "}
          <AnchorLink id={reason.evidence.anchor_paper_id} title={reason.evidence.anchor_title} onOpenPaper={onOpenPaper} />.
        </p>
      );
    case "draft_match":
      if ("deleted_document" in reason.evidence) return <p>Matched a private document that has since been deleted.</p>;
      return (
        <div className="flex flex-col gap-2">
          <p className="flex flex-wrap items-center gap-1.5">
            <FileText aria-hidden="true" className="size-3.5" />
            <span className="font-medium text-text">{reason.evidence.filename}</span>
            <span>· {reason.evidence.section}</span>
            {reason.evidence.page ? <span>· p. {reason.evidence.page}</span> : null}
          </p>
          <blockquote className="border-l-2 border-accent/40 pl-3 font-serif text-[14px] leading-relaxed text-text italic">
            {reason.evidence.snippet}
          </blockquote>
          <p className="text-[12px] text-faint">This passage is private and never leaves the server.</p>
        </div>
      );
    case "preference":
      return (
        <p>
          A model trained on your {reason.evidence.n_labels} ratings in this project gives it a{" "}
          {Math.round(reason.evidence.probability * 100)}% chance of being relevant.
        </p>
      );
    case "category":
      return <p>Listed in {categoryLabel(reason.evidence.category)}, one of this project's arXiv categories.</p>;
    case "recent":
      return <p>{reason.evidence.days === 0 ? "Published today." : `Published ${reason.evidence.days} day${reason.evidence.days === 1 ? "" : "s"} ago.`}</p>;
    case "keyword":
      return (
        <ul className="flex flex-col gap-1.5">
          {reason.evidence.matches.map((match) => (
            <li key={`${match.field}-${match.keyword}`} className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
              <span>
                Your keyword <span className="font-medium text-text">‘{match.keyword}’</span> appears in the {match.field} as
              </span>
              <mark className="rounded-[4px] bg-feat-keyword/15 px-1 font-serif text-[14px] text-text">{match.text}</mark>
            </li>
          ))}
        </ul>
      );
  }
}

function ReasonRow({ reason, onOpenPaper }: { reason: Reason; onOpenPaper: (paperId: number) => void }) {
  const [open, setOpen] = useState(false);
  const Icon = reason.kind === "metadata" ? BadgeCheck : Sparkles;
  return (
    <li className="rounded-control border border-border bg-surface-2/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-[13.5px] text-text"
      >
        <Icon aria-hidden="true" className={clsx("size-4 shrink-0", reason.kind === "metadata" ? "text-relevant" : "text-accent")} />
        <span className="min-w-0 flex-1">{reason.text}</span>
        {reason.contribution !== null ? <span className="font-mono text-[11.5px] text-faint">+{formatScore(reason.contribution, 2)}</span> : null}
        <ChevronDown aria-hidden="true" className={clsx("size-4 shrink-0 text-faint transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-3 pl-9 text-[13px] leading-relaxed text-muted">
              <Evidence reason={reason} onOpenPaper={onOpenPaper} />
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </li>
  );
}

export function WhyThisPaper({ reasons, onOpenPaper }: { reasons: readonly Reason[]; onOpenPaper: (paperId: number) => void }) {
  const sources = reasons.filter((reason) => reason.kind === "metadata");
  const inferred = reasons.filter((reason) => reason.kind === "model");
  if (reasons.length === 0) return <p className="text-[13.5px] text-muted">No specific reason was recorded for this paper.</p>;
  return (
    <div className="flex flex-col gap-4">
      {sources.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h4 className="text-eyebrow">From the sources</h4>
          <ul className="flex flex-col gap-1.5">
            {sources.map((reason, index) => (
              <ReasonRow key={`${reason.code}-${index}`} reason={reason} onOpenPaper={onOpenPaper} />
            ))}
          </ul>
        </section>
      ) : null}
      {inferred.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h4 className="text-eyebrow">Inferred by Ratiocination</h4>
          <ul className="flex flex-col gap-1.5">
            {inferred.map((reason, index) => (
              <ReasonRow key={`${reason.code}-${index}`} reason={reason} onOpenPaper={onOpenPaper} />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
