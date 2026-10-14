import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphGroup, GraphNode } from "../../api/types";
import { makeGraphEdge, makeGraphNode, makePaperSummary } from "../../test/fixtures";
import { radialLayout, type RadialLayout } from "./layout";

const work = (id: number) => makeGraphNode({ id: `work:${id}`, paper: makePaperSummary({ id }) });
const scholar = (id: number) => makeGraphNode({ id: `scholar:${id}`, type: "scholar", label: `Author ${id}`, paper: null });
const topic = (id: number) => makeGraphNode({ id: `concept:${id}`, type: "concept", label: `Topic ${id}`, paper: null });
const group = (key: GraphGroup["key"], label: string, ids: string[], count = ids.length): GraphGroup => ({ key, label, node_ids: ids, count });
const range = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, i) => from + i);
const ids = (nodes: readonly GraphNode[]) => nodes.map((node) => node.id);
const authored = (author: string, target: string): GraphEdge =>
  makeGraphEdge({ id: `e:${author}:${target}`, source: author, target, relation: "AUTHORED", evidence: null });

function column(layout: RadialLayout, side: "left" | "right") {
  const rows = [
    ...[...layout.positions].filter(([id]) => layout.sides.get(id) === side).map(([id, p]) => ({ id, ...p })),
    ...layout.headers.filter((header) => header.side === side).map((header) => ({ id: header.id, ...header.position })),
  ];
  return rows.sort((a, b) => a.y - b.y);
}

describe("radialLayout", () => {
  const center = makeGraphNode({ id: "work:1", is_center: true });
  const references = range(10, 15).map(work);
  const similar = range(20, 23).map(work);
  const topics = [topic(5)];
  const groups = [
    group("references", "References", ids(references), 40),
    group("similar", "Similar papers", ids(similar)),
    group("topics", "Topics", ids(topics)),
  ];
  const nodes = [center, ...references, ...similar, ...topics];

  it("puts the paper in the middle and every neighbour in a column, labels pointing outwards", () => {
    const layout = radialLayout(nodes, [], groups, "work:1");
    expect(layout.positions.get("work:1")).toEqual({ x: 0, y: 0 });
    expect(layout.sides.get("work:1")).toBe("below");
    for (const node of nodes.slice(1)) {
      const position = layout.positions.get(node.id);
      expect(position, node.id).toBeDefined();
      expect(layout.sides.get(node.id)).toBe((position?.x ?? 0) < 0 ? "left" : "right");
    }
    const spots = new Set([...layout.positions.values()].map((p) => `${p.x},${p.y}`));
    expect(spots.size).toBe(nodes.length);
  });

  it("reads the groups down the left column, then the right, cutting between groups", () => {
    const layout = radialLayout(nodes, [], groups, "work:1");
    expect(column(layout, "left").map((row) => row.id)).toEqual(["header:references:left", ...ids(references)]);
    expect(column(layout, "right").map((row) => row.id)).toEqual([
      "header:similar:right",
      ...ids(similar),
      "header:topics:right",
      "concept:5",
    ]);
    // "of" only when the server knows more than it sent.
    expect(layout.headers.map((header) => header.text)).toEqual(["References · 6 of 40", "Similar papers · 4", "Topics · 1"]);
  });

  it("gives every label a row of its own", () => {
    const layout = radialLayout(nodes, [], groups, "work:1");
    for (const side of ["left", "right"] as const) {
      const rows = column(layout, side);
      for (let i = 1; i < rows.length; i += 1) {
        expect((rows[i]?.y ?? 0) - (rows[i - 1]?.y ?? 0)).toBeGreaterThanOrEqual(20);
      }
    }
  });

  it("splits one tall group over both columns, the second under a continued header", () => {
    const many = range(10, 39).map(work);
    const layout = radialLayout([center, ...many], [], [group("references", "References", ids(many))], "work:1");
    const left = column(layout, "left");
    const right = column(layout, "right");
    expect(left[0]?.id).toBe("header:references:left");
    expect(right[0]?.id).toBe("header:references:right");
    expect(layout.headers.find((header) => header.side === "right")?.text).toBe("References (cont.)");
    expect(Math.abs(left.length - right.length)).toBeLessThanOrEqual(2);
    // Reading order is kept: the left column holds the first references.
    expect(left.slice(1).map((row) => row.id)).toEqual(ids(many).slice(0, left.length - 1));
  });

  it("indents each author's papers under the author", () => {
    const authors = [scholar(1), scholar(2)];
    const works = [work(30), work(31), work(32)];
    const layout = radialLayout(
      [center, ...authors, ...works],
      [authored("scholar:1", "work:1"), authored("scholar:1", "work:30"), authored("scholar:1", "work:31"), authored("scholar:2", "work:31"), authored("scholar:2", "work:32")],
      [group("authors", "Authors", ids(authors)), group("author_works", "Authors' most disruptive papers", ids(works))],
      "work:1",
    );
    const at = (id: string) => layout.positions.get(id) ?? { x: NaN, y: NaN };
    // A paper by both authors sits under the first one.
    for (const [author, papers] of [["scholar:1", ["work:30", "work:31"]], ["scholar:2", ["work:32"]]] as const) {
      for (const paper of papers) {
        expect(layout.sides.get(paper)).toBe(layout.sides.get(author));
        expect(Math.abs(at(paper).x)).toBeGreaterThan(Math.abs(at(author).x));
        expect(at(paper).y).toBeGreaterThan(at(author).y);
      }
    }
    expect(layout.headers.map((header) => header.text)).toEqual(["Authors · 2"]);
  });

  it("does not show a node twice and still places nodes outside every group", () => {
    const stray = work(99);
    const both = group("similar", "Similar papers", ["work:10", "work:20"]);
    const layout = radialLayout([center, ...references, similar[0]!, stray], [], [groups[0]!, both], "work:1");
    expect(column(layout, "right").concat(column(layout, "left")).filter((row) => row.id === "work:10")).toHaveLength(1);
    expect(layout.headers.find((header) => header.id.startsWith("header:similar"))?.text).toBe("Similar papers · 1");
    expect(layout.positions.get("work:99")).toBeDefined();
  });
});
