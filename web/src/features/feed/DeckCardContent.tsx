/** The body of a deck card (UI.md §7): chips, title, byline, abstract, reasons and links. */
import { ExternalLink, MessageCircleQuestion } from "lucide-react";
import { useState } from "react";
import type { RecommendationItem } from "../../api/types";
import { formatAuthors } from "../../lib/format";
import { PaperChips, ReasonPill, paperLinks } from "../paper/meta";

export function DeckCardContent({ item, onWhy }: { item: RecommendationItem; onWhy: () => void }) {
  const { paper } = item;
  const [expanded, setExpanded] = useState(false);
  const byline = [formatAuthors(paper.authors, 3), [paper.venue, paper.year].filter(Boolean).join(" ")].filter(Boolean).join(" · ");
  const abstract = paper.abstract?.trim();
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-5 sm:p-7">
      <PaperChips paper={paper} />
      <h2 id={`paper-title-${paper.id}`} className="line-clamp-3 font-serif text-[20px] leading-[1.25] font-semibold tracking-[-0.01em] text-text sm:text-[24px]">
        {paper.title}
      </h2>
      {byline ? <p className="-mt-2 truncate text-[13.5px] text-muted">{byline}</p> : null}
      <div className="flex min-h-0 flex-1 flex-col gap-1.5">
        <div className="min-h-0 overflow-y-auto pr-1" onPointerDownCapture={(event) => expanded && event.stopPropagation()}>
          {abstract ? (
            <p className={`paper-abstract text-[15px] sm:text-[15.5px] ${expanded ? "" : "line-clamp-3 sm:line-clamp-6"}`}>{abstract}</p>
          ) : (
            <p className="text-[14px] text-faint italic">Abstract not available from the sources.</p>
          )}
        </div>
        {abstract && abstract.length > 300 ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            onPointerDownCapture={(event) => event.stopPropagation()}
            aria-expanded={expanded}
            className="self-start text-[13px] font-medium text-accent hover:underline"
          >
            {expanded ? "Less" : "More"}
          </button>
        ) : null}
      </div>
      {item.reasons.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Why this paper">
          {item.reasons.slice(0, 3).map((reason, index) => (
            <li key={`${reason.code}-${index}`} className="max-w-full">
              <ReasonPill reason={reason} />
            </li>
          ))}
        </ul>
      ) : null}
      <footer className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-3.5">
        {paperLinks(paper).map((link) => (
          <a
            key={link.label}
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            onPointerDownCapture={(event) => event.stopPropagation()}
            className="inline-flex items-center gap-1 text-[13px] font-medium text-muted transition-colors hover:text-text"
          >
            {link.label}
            <ExternalLink aria-hidden="true" className="size-3" />
          </a>
        ))}
        <button
          type="button"
          onClick={onWhy}
          onPointerDownCapture={(event) => event.stopPropagation()}
          className="ml-auto inline-flex items-center gap-1.5 text-[13px] font-medium text-accent hover:underline"
        >
          <MessageCircleQuestion aria-hidden="true" className="size-4" />
          Why this paper?
        </button>
      </footer>
    </div>
  );
}
