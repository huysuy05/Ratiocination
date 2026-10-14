/** Graph helpers shared by the canvas, the list view and the side panel. */
import type { GraphEdge, GraphNode, Relation } from "../../api/types";

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export const RELATIONS: readonly Relation[] = ["CITES", "SEMANTICALLY_SIMILAR", "HAS_TOPIC", "AUTHORED"];

export const RELATION_TEXT: Readonly<Record<Relation, { label: string; legend: string; color: string }>> = {
  CITES: { label: "Citations", legend: "cites", color: "var(--edge-cites)" },
  SEMANTICALLY_SIMILAR: { label: "Similar", legend: "similar (inferred)", color: "var(--edge-similar)" },
  HAS_TOPIC: { label: "Topics", legend: "topic (OpenAlex)", color: "var(--edge-topic)" },
  AUTHORED: { label: "Authors", legend: "authored", color: "var(--edge-authored)" },
};

/** The work's paper id from a node id ("work:12" → 12), else null. */
export function workPaperId(nodeId: string | null | undefined): number | null {
  const match = /^work:(\d+)$/.exec(nodeId ?? "");
  return match ? Number(match[1]) : null;
}

/** The scholar id from a node id ("scholar:3" → 3), else null. */
export function scholarId(nodeId: string | null | undefined): number | null {
  const match = /^scholar:(\d+)$/.exec(nodeId ?? "");
  return match ? Number(match[1]) : null;
}

export function edgesByNode(edges: readonly GraphEdge[]): Map<string, GraphEdge[]> {
  const out = new Map<string, GraphEdge[]>();
  for (const edge of edges) {
    for (const id of [edge.source, edge.target]) {
      const list = out.get(id);
      if (list) list.push(edge);
      else out.set(id, [edge]);
    }
  }
  return out;
}

export function relationCounts(edges: readonly GraphEdge[]): Record<Relation, number> {
  const counts: Record<Relation, number> = { CITES: 0, SEMANTICALLY_SIMILAR: 0, HAS_TOPIC: 0, AUTHORED: 0 };
  for (const edge of edges) counts[edge.relation] += 1;
  return counts;
}

/** One line describing an edge from `fromId`'s point of view. */
export function describeEdge(edge: GraphEdge, fromId: string, labelOf: (id: string) => string): string {
  const other = labelOf(edge.source === fromId ? edge.target : edge.source);
  switch (edge.relation) {
    case "CITES":
      return edge.source === fromId ? `Cites “${other}”` : `Cited by “${other}”`;
    case "SEMANTICALLY_SIMILAR":
      return `Similar to “${other}”${edge.confidence !== null ? ` (cosine ${edge.confidence.toFixed(2)})` : ""}`;
    case "HAS_TOPIC":
      return edge.source === fromId ? `Topic: ${other}` : `Paper on this topic: “${other}”`;
    case "AUTHORED":
      return edge.source === fromId ? `Wrote “${other}”` : `Written by ${other}`;
  }
}
