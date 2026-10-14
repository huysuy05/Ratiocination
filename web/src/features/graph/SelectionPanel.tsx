/**
 * Details of the selected node or edge (UI.md §9): a work with its actions, a scholar with their
 * works by disruption, a topic, or a link with its evidence.
 */
import { Crosshair, Network, PanelRightOpen, X } from "lucide-react";
import { motion } from "motion/react";
import { toast } from "sonner";
import { errorMessage } from "../../api/client";
import { useExpandPaper, useScholar } from "../../api/hooks";
import type { GraphEdge, GraphNode } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { IconButton } from "../../components/ui/IconButton";
import { Skeleton } from "../../components/ui/Skeleton";
import { formatAuthors, formatNumber } from "../../lib/format";
import { STATE_META } from "../../lib/states";
import { DISRUPTION_COLOR, PaperChips, cdLabelOf, formatCd } from "../paper/meta";
import { RELATION_TEXT, scholarId, workPaperId } from "./model";

function ScholarWorks({ id, onOpen }: { id: number; onOpen: (paperId: number) => void }) {
  const scholar = useScholar(id);
  if (scholar.isPending) return <Skeleton className="h-24" />;
  if (scholar.isError) return <p className="text-[12.5px] text-not-relevant">{scholar.error.message}</p>;
  if (scholar.data.works.length === 0) return <p className="text-[12.5px] text-muted">No analysed works yet.</p>;
  return (
    <ol className="flex flex-col gap-1.5">
      {scholar.data.works.slice(0, 6).map((work) => (
        <li key={work.paper.id} className="flex items-start gap-2">
          <span className="w-12 shrink-0 text-right font-mono text-[12px]" style={{ color: DISRUPTION_COLOR[cdLabelOf(work.cd)] }}>
            {formatCd(work.cd)}
          </span>
          <button type="button" onClick={() => onOpen(work.paper.id)} className="text-left font-serif text-[13px] leading-snug text-text hover:text-accent">
            {work.paper.title}
          </button>
        </li>
      ))}
    </ol>
  );
}

export interface SelectionPanelProps {
  node: GraphNode | null;
  edge: GraphEdge | null;
  labelOf: (id: string) => string;
  projectId: number | null;
  onOpen: (paperId: number) => void;
  onCenter: ((paperId: number) => void) | null;
  onClose: () => void;
}

export function SelectionPanel({ node, edge, labelOf, projectId, onOpen, onCenter, onClose }: SelectionPanelProps) {
  const expand = useExpandPaper();
  if (!node && !edge) return null;
  const paperId = workPaperId(node?.id);
  const scholar = scholarId(node?.id);

  return (
    <motion.aside
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 16 }}
      aria-label="Selection"
      className="pointer-events-auto flex max-h-full w-full flex-col gap-3 overflow-y-auto rounded-panel border border-border bg-surface/95 p-4 backdrop-blur-md elevation-overlay sm:w-80"
    >
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 text-eyebrow">
          {edge ? RELATION_TEXT[edge.relation].label : node?.type === "work" ? "Paper" : node?.type === "concept" ? "Topic" : "Author"}
        </p>
        <IconButton label="Close details" icon={<X />} variant="ghost" size="sm" onClick={onClose} />
      </div>

      {node?.paper ? (
        <>
          <PaperChips paper={node.paper} />
          <p className="font-serif text-[16px] leading-snug font-semibold text-text">{node.paper.title}</p>
          <p className="text-[12.5px] text-muted">{[formatAuthors(node.paper.authors, 3), node.paper.year].filter(Boolean).join(" · ")}</p>
          {node.state ? (
            <p className="text-[12.5px] font-medium" style={{ color: STATE_META[node.state].color }}>
              {STATE_META[node.state].label}
              {node.seed ? " · Seed" : ""}
            </p>
          ) : node.seed ? (
            <p className="text-[12.5px] font-medium text-accent">Seed paper</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {paperId !== null ? (
              <Button size="sm" variant="primary" leadingIcon={<PanelRightOpen aria-hidden="true" />} onClick={() => onOpen(paperId)}>
                Details
              </Button>
            ) : null}
            {paperId !== null && onCenter && !node.is_center ? (
              <Button size="sm" leadingIcon={<Crosshair aria-hidden="true" />} onClick={() => onCenter(paperId)}>
                Center here
              </Button>
            ) : null}
            {paperId !== null ? (
              <Button
                size="sm"
                variant="ghost"
                leadingIcon={<Network aria-hidden="true" />}
                loading={expand.isPending}
                onClick={() =>
                  expand.mutate(
                    { paperId, projectId },
                    {
                      onSuccess: (result) => toast(result.job ? "Loading its citations" : (result.message ?? "Citations are up to date")),
                      onError: (error) => toast.error(errorMessage(error, "Could not load citations")),
                    },
                  )
                }
              >
                Load citations
              </Button>
            ) : null}
          </div>
        </>
      ) : null}

      {node?.scholar ? (
        <>
          <p className="text-[16px] font-semibold text-text">{node.scholar.display_name}</p>
          <p className="text-[12.5px] text-muted">
            {[
              node.scholar.institution,
              node.scholar.h_index !== null ? `h-index ${node.scholar.h_index}` : null,
              node.scholar.cited_by_count !== null ? `${formatNumber(node.scholar.cited_by_count)} citations` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {scholar !== null ? (
            <section className="flex flex-col gap-2">
              <h3 className="text-[12px] font-semibold text-faint">Works by disruption</h3>
              <ScholarWorks id={scholar} onOpen={onOpen} />
            </section>
          ) : null}
        </>
      ) : null}

      {node && node.type === "concept" ? <p className="text-[15px] font-semibold text-text">{node.label}</p> : null}
      {node && node.type === "author" ? (
        <>
          <p className="text-[15px] font-semibold text-text">{node.label}</p>
          <p className="text-[12.5px] text-muted">Not linked to an OpenAlex author yet. Analyse the paper's breakthroughs to link its authors.</p>
        </>
      ) : null}

      {edge ? (
        <div className="flex flex-col gap-2 text-[13px]">
          <p className="text-text">
            “{labelOf(edge.source)}” <span className="text-muted">{RELATION_TEXT[edge.relation].legend}</span> “{labelOf(edge.target)}”
          </p>
          <p className="text-[12.5px] text-muted">
            {edge.verified ? "From the sources" : "Inferred by Ratiocination"} · {edge.provenance}
            {edge.confidence !== null ? ` · ${edge.relation === "SEMANTICALLY_SIMILAR" ? "cosine" : "score"} ${edge.confidence.toFixed(2)}` : ""}
          </p>
          {edge.evidence?.relation === "CITES" ? (
            <>
              {edge.evidence.intents.length > 0 ? <p className="text-[12.5px] text-muted">Intent: {edge.evidence.intents.join(", ")}</p> : null}
              {edge.evidence.contexts.slice(0, 3).map((context, index) => (
                <blockquote key={index} className="border-l-2 border-edge-cites/40 pl-2.5 font-serif text-[13px] text-text italic">
                  “{context}”
                </blockquote>
              ))}
            </>
          ) : null}
        </div>
      ) : null}
    </motion.aside>
  );
}
