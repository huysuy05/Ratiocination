/**
 * The paper detail (UI.md §8): header, why this paper and its score, abstract, disruption, the
 * authors' breakthroughs, topics and connections. Rendered in the sheet and on `/papers/:id`.
 */
import clsx from "clsx";
import { Copy, ExternalLink, Network, Waypoints } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { errorMessage } from "../../api/client";
import { useExpandPaper } from "../../api/hooks";
import type { PaperDetail } from "../../api/types";
import { paths } from "../../app/paths";
import { Button } from "../../components/ui/Button";
import { IconButton } from "../../components/ui/IconButton";
import { formatDate, formatNumber, formatRelativeDate } from "../../lib/format";
import { categoryLabel } from "../projects/categories";
import { Breakthroughs } from "./Breakthroughs";
import { DisruptionCard } from "./DisruptionCard";
import { PaperChips, paperLinks } from "./meta";
import { ScoreBreakdown } from "./ScoreBreakdown";
import { WhyThisPaper } from "./WhyThisPaper";

const AUTHORS_SHOWN = 8;

function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("flex flex-col gap-3", className)}>
      <h3 className="text-[12px] font-semibold tracking-[0.08em] text-faint uppercase">{title}</h3>
      {children}
    </section>
  );
}

function IdentifierRow({ label, value }: { label: string; value: string }) {
  const copy = () => {
    void navigator.clipboard?.writeText(value).then(
      () => toast(`${label} copied`),
      () => toast.error("Could not copy"),
    );
  };
  return (
    <div className="flex items-center gap-2 text-[12.5px]">
      <span className="w-16 shrink-0 text-faint">{label}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-muted">{value}</span>
      <IconButton label={`Copy ${label}`} icon={<Copy />} variant="ghost" size="sm" onClick={copy} />
    </div>
  );
}

function Connections({ paper, projectId }: { paper: PaperDetail; projectId: number | null }) {
  const expand = useExpandPaper();
  const { expansion } = paper;
  const pending = expansion.status === "queued" || expansion.status === "running" || expand.isPending;
  const load = (force = false) =>
    expand.mutate(
      { paperId: paper.id, projectId, force },
      {
        onSuccess: (result) => toast(result.job ? "Loading citations in the background" : (result.message ?? "Citations are up to date")),
        onError: (error) => toast.error(errorMessage(error, "Could not load citations")),
      },
    );
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13.5px] text-muted">
        {paper.counts.references !== null || paper.counts.citations !== null
          ? `${formatNumber(paper.counts.references ?? 0)} references and ${formatNumber(paper.counts.citations ?? 0)} citing papers known here.`
          : "Citations have not been loaded for this paper yet."}{" "}
        {expansion.last_expanded_at ? `Loaded ${formatRelativeDate(expansion.last_expanded_at)}.` : null}
        {expansion.message ? ` ${expansion.message}.` : null}
      </p>
      <div className="flex flex-wrap gap-2">
        {projectId !== null ? (
          <Button asChild size="sm" variant="primary" leadingIcon={<Waypoints aria-hidden="true" />}>
            <Link to={paths.graph(projectId, paper.id)}>Open in graph</Link>
          </Button>
        ) : null}
        <Button size="sm" leadingIcon={<Network aria-hidden="true" />} onClick={() => load(expansion.status === "done")} loading={pending}>
          {pending ? "Loading citations…" : expansion.status === "done" ? "Refresh citations" : "Load citations"}
        </Button>
      </div>
    </div>
  );
}

export interface PaperDetailContentProps {
  paper: PaperDetail;
  projectId: number | null;
  onOpenPaper: (paperId: number) => void;
  /**
   * "h1" on the paper page; "none" in the sheet, whose dialog title already is the heading (a
   * second heading with the same text would be announced twice).
   */
  headingLevel?: "h1" | "h2" | "none";
}

export function PaperDetailContent({ paper, projectId, onOpenPaper, headingLevel = "h2" }: PaperDetailContentProps) {
  const Heading = headingLevel === "none" ? "p" : headingLevel;
  const [allAuthors, setAllAuthors] = useState(false);
  const authors = [...paper.authors].sort((a, b) => a.position - b.position);
  const shownAuthors = allAuthors ? authors : authors.slice(0, AUTHORS_SHOWN);
  const links = paperLinks(paper);
  const identifiers: [string, string][] = [];
  for (const [label, value] of [
    ["arXiv", paper.identifiers.arxiv],
    ["DOI", paper.identifiers.doi],
    ["OpenAlex", paper.identifiers.openalex],
    ["S2", paper.identifiers.s2],
  ] as const) {
    if (value) identifiers.push([label, value]);
  }

  return (
    <article className="flex flex-col gap-8">
      <header className="flex flex-col gap-3">
        <PaperChips paper={paper} />
        <Heading
          id={headingLevel === "h1" ? "page-title" : `paper-title-${paper.id}`}
          className="font-serif text-[26px] leading-[1.22] font-semibold tracking-[-0.012em] text-text md:text-[30px]"
        >
          {paper.title}
        </Heading>
        {authors.length > 0 ? (
          <p className="text-[14px] leading-relaxed text-muted">
            {shownAuthors.map((author) => author.name).join(", ")}
            {authors.length > AUTHORS_SHOWN ? (
              <>
                {allAuthors ? " " : " … "}
                <button type="button" onClick={() => setAllAuthors((v) => !v)} className="font-medium text-accent hover:underline">
                  {allAuthors ? "Show fewer" : `all ${authors.length} authors`}
                </button>
              </>
            ) : null}
          </p>
        ) : null}
        <p className="text-[13px] text-faint">
          {[paper.publication_date ? formatDate(paper.publication_date) : paper.year, paper.venue, paper.categories.map(categoryLabel).join(", ")]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {links.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {links.map((link) => (
              <Button key={link.label} asChild size="sm" variant="outline" trailingIcon={<ExternalLink aria-hidden="true" />}>
                <a href={link.href} target="_blank" rel="noopener noreferrer">
                  {link.label}
                </a>
              </Button>
            ))}
          </div>
        ) : null}
      </header>

      {paper.recommendation ? (
        <Section title="Why this paper">
          <WhyThisPaper reasons={paper.recommendation.reasons} onOpenPaper={onOpenPaper} />
          <details className="group rounded-panel border border-border bg-surface-2/30 px-4 py-3">
            <summary className="cursor-pointer text-[13px] font-medium text-muted marker:text-faint group-open:mb-4">Score breakdown (for auditing)</summary>
            <ScoreBreakdown item={paper.recommendation} />
          </details>
        </Section>
      ) : null}

      <Section title="Abstract">
        {paper.abstract ? (
          <p className="paper-abstract text-[16px]">{paper.abstract}</p>
        ) : (
          <p className="text-[14px] text-faint italic">Abstract not available from the sources.</p>
        )}
      </Section>

      <Section title="Disruption">
        <DisruptionCard paperId={paper.id} detail={paper.disruption} />
      </Section>

      <Section title="The authors' breakthroughs">
        <Breakthroughs paperId={paper.id} onOpenPaper={onOpenPaper} />
      </Section>

      {paper.topics.length > 0 ? (
        <Section title="Topics (OpenAlex)">
          <ul className="flex flex-wrap gap-1.5">
            {paper.topics.map((topic) => (
              <li key={topic.concept_id} className="rounded-pill border border-edge-topic/30 bg-edge-topic/8 px-2.5 py-1 text-[12.5px] text-text">
                {topic.label}
                {topic.score !== null ? <span className="ml-1.5 text-faint tabular-nums">{Math.round(topic.score * 100)}%</span> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Connections">
        <Connections paper={paper} projectId={projectId} />
      </Section>

      {identifiers.length > 0 ? (
        <Section title="Identifiers">
          <div className="flex flex-col gap-0.5">
            {identifiers.map(([label, value]) => (
              <IdentifierRow key={label} label={label} value={value} />
            ))}
          </div>
        </Section>
      ) : null}
    </article>
  );
}
