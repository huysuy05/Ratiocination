/**
 * The graph as lists (UI.md §9): the canvas's accessible equivalent and the default on phones.
 * Rows follow the API's groups; each work can be opened or centred, and every row discloses its links
 * (relation, provenance, verified or inferred, confidence, citation contexts).
 */
import clsx from "clsx";
import { ChevronDown, Crosshair, FileText, Hash, PanelRightOpen, UserRound } from "lucide-react";
import { useState } from "react";
import type { GraphEdge, GraphGroup, GraphNode } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { formatAuthors } from "../../lib/format";
import { STATE_META } from "../../lib/states";
import { describeEdge, workPaperId } from "./model";

function NodeIcon({ node }: { node: GraphNode }) {
  const Icon = node.type === "work" ? FileText : node.type === "concept" ? Hash : UserRound;
  const color = node.state ? STATE_META[node.state].color : node.type === "concept" ? "var(--edge-topic)" : node.type === "work" ? "var(--accent)" : "var(--edge-authored)";
  return <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" style={{ color }} />;
}

function Row({
  node,
  links,
  labelOf,
  onOpen,
  onCenter,
}: {
  node: GraphNode;
  links: readonly GraphEdge[];
  labelOf: (id: string) => string;
  onOpen: (paperId: number) => void;
  onCenter: ((paperId: number) => void) | null;
}) {
  const [open, setOpen] = useState(false);
  const paperId = workPaperId(node.id);
  return (
    <li className="rounded-control border border-border bg-surface px-3.5 py-3">
      <div className="flex items-start gap-3">
        <NodeIcon node={node} />
        <div className="min-w-0 flex-1">
          <p className={clsx("leading-snug text-text", node.type === "work" ? "font-serif text-[15px]" : "text-[14px] font-medium")}>{node.label}</p>
          <p className="mt-0.5 truncate text-[12px] text-muted">
            {node.paper
              ? [formatAuthors(node.paper.authors, 2), node.paper.year, node.state ? STATE_META[node.state].label : null, node.seed ? "Seed" : null]
                  .filter(Boolean)
                  .join(" · ")
              : node.scholar
                ? [node.scholar.institution, node.scholar.h_index !== null ? `h-index ${node.scholar.h_index}` : null].filter(Boolean).join(" · ")
                : node.type === "concept"
                  ? "OpenAlex topic"
                  : node.type === "author"
                    ? "Author (not linked to OpenAlex yet)"
                    : null}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {paperId !== null ? (
            <Button size="sm" variant="ghost" leadingIcon={<PanelRightOpen aria-hidden="true" />} onClick={() => onOpen(paperId)}>
              Open
            </Button>
          ) : null}
          {paperId !== null && onCenter && !node.is_center ? (
            <Button size="sm" variant="ghost" leadingIcon={<Crosshair aria-hidden="true" />} onClick={() => onCenter(paperId)}>
              Center
            </Button>
          ) : null}
        </div>
      </div>
      {links.length > 0 ? (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="mt-2 inline-flex items-center gap-1 text-[12px] font-medium text-muted hover:text-text"
          >
            <ChevronDown aria-hidden="true" className={clsx("size-3.5 transition-transform", open && "rotate-180")} />
            {links.length} link{links.length === 1 ? "" : "s"}
          </button>
          {open ? (
            <ul className="mt-2 flex flex-col gap-1.5 border-l-2 border-border pl-3">
              {links.map((edge) => (
                <li key={edge.id} className="text-[12.5px] text-muted">
                  <span className="text-text">{describeEdge(edge, node.id, labelOf)}</span>
                  <span className="text-faint">
                    {" "}
                    · {edge.verified ? "from sources" : "inferred"} · {edge.provenance}
                  </span>
                  {edge.evidence?.relation === "CITES" && edge.evidence.contexts.length > 0 ? (
                    <blockquote className="mt-1 font-serif text-[13px] italic">“{edge.evidence.contexts[0]}”</blockquote>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </li>
  );
}

export interface GraphListProps {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  groups: readonly GraphGroup[];
  onOpen: (paperId: number) => void;
  onCenter: ((paperId: number) => void) | null;
}

export function GraphList({ nodes, edges, groups, onOpen, onCenter }: GraphListProps) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links = new Map<string, GraphEdge[]>();
  for (const edge of edges) {
    for (const id of [edge.source, edge.target]) links.set(id, [...(links.get(id) ?? []), edge]);
  }
  const labelOf = (id: string) => byId.get(id)?.label ?? id;
  const center = nodes.find((node) => node.is_center);
  const visibleGroups = groups.filter((group) => group.node_ids.length > 0);
  return (
    <div className="flex flex-col gap-6">
      {center ? (
        <section aria-label="Center paper">
          <ul>
            <Row node={center} links={links.get(center.id) ?? []} labelOf={labelOf} onOpen={onOpen} onCenter={onCenter} />
          </ul>
        </section>
      ) : null}
      {visibleGroups.length === 0 ? <p className="text-[13.5px] text-muted">No connections known yet.</p> : null}
      {visibleGroups.map((group) => (
        <section key={group.key} className="flex flex-col gap-2">
          <h2 className="text-[13px] font-semibold text-text">
            {group.label}{" "}
            <span className="font-normal text-faint">
              {group.count > group.node_ids.length ? `${group.node_ids.length} of ${group.count}` : group.count}
            </span>
          </h2>
          <ul className="flex flex-col gap-2">
            {group.node_ids.map((id) => {
              const node = byId.get(id);
              return node ? <Row key={id} node={node} links={links.get(id) ?? []} labelOf={labelOf} onOpen={onOpen} onCenter={onCenter} /> : null;
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
