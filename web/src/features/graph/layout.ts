/**
 * Layout of a paper's neighbourhood (UI.md §9): the paper in the middle, its neighbours in two
 * columns that curve around it like the halves of a wheel. Each group (references, citing papers,
 * similar papers, topics, authors) is a run of rows under a small header, and each author's papers
 * sit indented under the author. The groups are read in order — down the left column, then down the
 * right one — and cut into the two columns where their heights balance best, preferring a cut
 * between groups. Every label points outwards on its own row, so labels never overlap. Pure:
 * positions only, the canvas applies them.
 */
import type { GraphEdge, GraphGroup, GraphNode, GroupKey } from "../../api/types";

export interface Point {
  x: number;
  y: number;
}

export type Side = "left" | "right";
export type LabelSide = Side | "below";

export interface Header {
  id: string;
  text: string;
  side: Side;
  position: Point;
}

export interface RadialLayout {
  positions: Map<string, Point>;
  sides: Map<string, LabelSide>;
  headers: Header[];
}

/** Reading order of the groups: down the left column, then down the right one. */
const ORDER: readonly GroupKey[] = ["references", "citations", "similar", "seeds", "relevant", "saved", "topics", "authors"];

type RowKind = "header" | "item" | "child";

/** Row heights in px: one label per row, with room above each header. */
const HEIGHT: Readonly<Record<RowKind, number>> = { header: 32, item: 24, child: 20 };
/** How far an author's papers sit outside the author. */
const INDENT = 30;
/** Imbalance (px) worth accepting to keep a group in one column. */
const SPLIT_COST = 150;
/** The columns use this share of the wheel's height; the rest keeps the two tops apart. */
const SPAN = 0.86;
const MIN_RY = 170;
const MIN_RX = 200;
const MAX_RX = 380;

interface Row {
  id: string;
  kind: RowKind;
  text?: string;
}

interface Block {
  key: string;
  title: string | null;
  header: string | null;
  /** A unit never splits across columns (an author stays with their papers). */
  units: Row[][];
}

const sum = (values: readonly number[]) => values.reduce((total, value) => total + value, 0);
const rowsHeight = (rows: readonly Row[]) => sum(rows.map((row) => HEIGHT[row.kind]));

/** "References · 25 of 40": "of" only when the server sent fewer than it knows of (a node already
 * shown under an earlier header, such as a reference that is also similar, is not counted again). */
function headerText(group: GraphGroup, rows: number): string {
  return group.count > group.node_ids.length ? `${group.label} · ${rows} of ${group.count}` : `${group.label} · ${rows}`;
}

function blocksOf(nodes: readonly GraphNode[], edges: readonly GraphEdge[], groups: readonly GraphGroup[], centerId: string): Block[] {
  const known = new Set(nodes.map((node) => node.id));
  const placed = new Set<string>([centerId]);
  const claim = (ids: readonly string[]) =>
    ids.filter((id) => {
      if (!known.has(id) || placed.has(id)) return false;
      placed.add(id);
      return true;
    });
  const byKey = new Map(groups.map((group) => [group.key, group]));
  const items = (ids: readonly string[]) => ids.map((id): Row[] => [{ id, kind: "item" }]);

  const blocks: Block[] = [];
  for (const key of ORDER) {
    const group = byKey.get(key);
    if (!group) continue;
    if (key !== "authors") {
      const ids = claim(group.node_ids);
      if (ids.length > 0) blocks.push({ key, title: group.label, header: headerText(group, ids.length), units: items(ids) });
      continue;
    }
    // Each author with their papers (most disruptive first), a paper under its first shown author.
    const authors = claim(group.node_ids);
    const wrote = new Map<string, Set<string>>();
    for (const edge of edges) {
      if (edge.relation !== "AUTHORED") continue;
      const works = wrote.get(edge.source) ?? new Set<string>();
      works.add(edge.target);
      wrote.set(edge.source, works);
    }
    const papers = new Map<string, string[]>(authors.map((author) => [author, []]));
    for (const work of byKey.get("author_works")?.node_ids ?? []) {
      const author = authors.find((candidate) => wrote.get(candidate)?.has(work));
      if (author && claim([work]).length > 0) papers.get(author)?.push(work);
    }
    if (authors.length > 0) {
      blocks.push({
        key,
        title: group.label,
        header: headerText(group, authors.length),
        units: authors.map((author) => [
          { id: author, kind: "item" },
          ...(papers.get(author) ?? []).map((id): Row => ({ id, kind: "child" })),
        ]),
      });
    }
  }
  const orphanWorks = byKey.get("author_works");
  if (orphanWorks) {
    const ids = claim(orphanWorks.node_ids);
    if (ids.length > 0) blocks.push({ key: "author_works", title: orphanWorks.label, header: headerText(orphanWorks, ids.length), units: items(ids) });
  }
  // Anything the groups missed still gets a row, so every node has a position.
  const rest = claim(nodes.map((node) => node.id));
  if (rest.length > 0) blocks.push({ key: "other", title: null, header: null, units: items(rest) });
  return blocks;
}

/**
 * What a cut costs: the left column `left` px tall, the right one `right` px tall; `splitsGroup`
 * when the cut falls inside a group, which then shows its header twice ("References (cont.)").
 * The cheapest cut wins.
 *
 * TODO(human): this default trades 1 px of height difference against SPLIT_COST px for a split.
 * Replace it with the policy you want the wheel to follow.
 */
function cutCost(left: number, right: number, splitsGroup: boolean): number {
  return Math.abs(left - right) + (splitsGroup ? SPLIT_COST : 0);
}

/** Where to cut the reading order: before unit `unit` of block `block` (unit 0: between blocks). */
function bestCut(blocks: readonly Block[]): { block: number; unit: number } {
  const headerHeight = (block: Block) => (block.header ? HEIGHT.header : 0);
  const heights = blocks.map((block) => headerHeight(block) + sum(block.units.map(rowsHeight)));
  const total = sum(heights);
  let best = { block: 0, unit: 0, cost: Infinity };
  let before = 0;
  blocks.forEach((block, index) => {
    const cost = cutCost(before, total - before, false);
    if (cost < best.cost) best = { block: index, unit: 0, cost };
    let left = before + headerHeight(block);
    for (let unit = 1; unit < block.units.length; unit += 1) {
      left += rowsHeight(block.units[unit - 1] ?? []);
      // The right column repeats the header ("… (cont.)").
      const split = cutCost(left, total - left + headerHeight(block), true);
      if (split < best.cost) best = { block: index, unit, cost: split };
    }
    before += heights[index] ?? 0;
  });
  const allLeft = cutCost(total, 0, false);
  if (allLeft < best.cost) best = { block: blocks.length, unit: 0, cost: allLeft };
  return best;
}

export function radialLayout(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  groups: readonly GraphGroup[],
  centerId: string,
): RadialLayout {
  const blocks = blocksOf(nodes, edges, groups, centerId);
  const cut = bestCut(blocks);
  const columns: Record<Side, Row[]> = { left: [], right: [] };
  blocks.forEach((block, index) => {
    const header = (side: Side, text: string | null): Row[] => (text ? [{ id: `header:${block.key}:${side}`, kind: "header", text }] : []);
    const units = block.units;
    if (index < cut.block) columns.left.push(...header("left", block.header), ...units.flat());
    else if (index > cut.block || cut.unit === 0) columns.right.push(...header("right", block.header), ...units.flat());
    else {
      columns.left.push(...header("left", block.header), ...units.slice(0, cut.unit).flat());
      columns.right.push(...header("right", block.title ? `${block.title} (cont.)` : null), ...units.slice(cut.unit).flat());
    }
  });

  const ry = Math.max(MIN_RY, Math.max(rowsHeight(columns.left), rowsHeight(columns.right)) / (2 * SPAN));
  const rx = Math.min(MAX_RX, Math.max(MIN_RX, ry * 0.78));
  const positions = new Map<string, Point>([[centerId, { x: 0, y: 0 }]]);
  const sides = new Map<string, LabelSide>([[centerId, "below"]]);
  const headers: Header[] = [];
  for (const side of ["left", "right"] as const) {
    const sign = side === "left" ? -1 : 1;
    let top = -rowsHeight(columns[side]) / 2;
    for (const row of columns[side]) {
      const height = HEIGHT[row.kind];
      // A header sits low in its row, close to the rows it heads.
      const y = row.kind === "header" ? top + height - 12 : top + height / 2;
      const x = rx * Math.sqrt(Math.max(0, 1 - (y / ry) ** 2)) + (row.kind === "child" ? INDENT : 0);
      const position = { x: Math.round(sign * x), y: Math.round(y) };
      if (row.kind === "header") headers.push({ id: row.id, text: row.text ?? "", side, position });
      else {
        positions.set(row.id, position);
        sides.set(row.id, side);
      }
      top += height;
    }
  }
  return { positions, sides, headers };
}
