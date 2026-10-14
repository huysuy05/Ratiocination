/**
 * Cytoscape styling for the knowledge graph (UI.md §9). Cytoscape cannot read CSS variables, so the
 * theme tokens are resolved from the document at runtime and the stylesheet is rebuilt on theme
 * change. Edge line styles carry the meaning on their own (never colour alone): CITES solid with an
 * arrow, SEMANTICALLY_SIMILAR dashed, HAS_TOPIC dotted, AUTHORED thin. A hovered or selected node
 * shows its full title.
 */
import type { StylesheetJson } from "cytoscape";
import type { GraphEdge, GraphNode } from "../../api/types";

export interface GraphColors {
  bg: string;
  surface: string;
  surface2: string;
  border: string;
  text: string;
  muted: string;
  faint: string;
  accent: string;
  relevant: string;
  notRelevant: string;
  save: string;
  cites: string;
  similar: string;
  topic: string;
  authored: string;
}

const TOKENS: Record<keyof GraphColors, string> = {
  bg: "--bg",
  surface: "--surface",
  surface2: "--surface-2",
  border: "--border-strong",
  text: "--text",
  muted: "--text-muted",
  faint: "--text-faint",
  accent: "--accent",
  relevant: "--relevant",
  notRelevant: "--not-relevant",
  save: "--save",
  cites: "--edge-cites",
  similar: "--edge-similar",
  topic: "--edge-topic",
  authored: "--edge-authored",
};

/** Theme colours from the document (falls back to the dark palette outside a browser). */
export function readGraphColors(): GraphColors {
  const fallback: GraphColors = {
    bg: "#11120d",
    surface: "#191a14",
    surface2: "#20211a",
    border: "rgba(216,207,188,.24)",
    text: "#fffbf4",
    muted: "#d8cfbc",
    faint: "#b3ab98",
    accent: "#d8cfbc",
    relevant: "#9db872",
    notRelevant: "#e08a72",
    save: "#d9b25e",
    cites: "#9db3c8",
    similar: "#c59cc0",
    topic: "#8cb8a8",
    authored: "#d99a6c",
  };
  if (typeof window === "undefined") return fallback;
  const computed = getComputedStyle(document.documentElement);
  const out = { ...fallback };
  for (const [key, token] of Object.entries(TOKENS) as [keyof GraphColors, string][]) {
    const value = computed.getPropertyValue(token).trim();
    if (value) out[key] = value;
  }
  return out;
}

/** Short node label for the canvas (full text stays in the list view and side panel). */
export function shortLabel(label: string, max = 34): string {
  const clean = label.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1).trimEnd()}…` : clean;
}

/** Node diameter: 14–36 px, or 10–20 px when `compact` (one row per node in the wheel layout). */
export function nodeSize(node: GraphNode, compact = false): number {
  if (node.type !== "work") return node.type === "concept" ? 20 : 16;
  const weight = typeof node.weight === "number" && Number.isFinite(node.weight) ? node.weight : 0.3;
  // Weights are a relevance in 0..1 or a citation count; squash both into 0..1.
  const scaled = Math.max(0, Math.min(1, weight <= 1 ? weight : Math.log10(1 + weight) / 4));
  return compact ? Math.round(10 + 10 * scaled) : Math.round(14 + 22 * scaled);
}

export function nodeData(node: GraphNode, compact = false): Record<string, unknown> {
  return {
    id: node.id,
    type: node.type,
    label: node.label,
    short: shortLabel(node.label, node.is_center ? 140 : 34),
    long: shortLabel(node.label, 120),
    state: node.state ?? "none",
    seed: node.seed ? "yes" : "no",
    center: node.is_center ? "yes" : "no",
    size: node.is_center ? Math.max(44, nodeSize(node)) : nodeSize(node, compact),
  };
}

export function edgeData(edge: GraphEdge): Record<string, unknown> {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    relation: edge.relation,
    verified: edge.verified ? "yes" : "no",
  };
}

export function graphStylesheet(c: GraphColors): StylesheetJson {
  return [
    {
      selector: "node",
      style: {
        width: "data(size)",
        height: "data(size)",
        "background-color": c.surface2,
        "border-width": 2,
        "border-color": c.accent,
        label: "data(short)",
        color: c.muted,
        "font-family": "Inter Variable, Inter, system-ui, sans-serif",
        "font-size": 10,
        "text-valign": "bottom",
        "text-margin-y": 5,
        "text-wrap": "ellipsis",
        "text-max-width": "140px",
        "text-outline-color": c.bg,
        "text-outline-width": 2,
        "min-zoomed-font-size": 7,
        "overlay-opacity": 0,
        "transition-property": "background-color, border-color, border-width, width, height",
        "transition-duration": 200,
      },
    },
    { selector: 'node[state = "relevant"]', style: { "background-color": c.relevant, "border-color": c.relevant } },
    { selector: 'node[state = "saved"]', style: { "background-color": c.save, "border-color": c.save } },
    { selector: 'node[state = "not_relevant"]', style: { "background-color": c.notRelevant, "border-color": c.notRelevant, opacity: 0.6 } },
    { selector: 'node[seed = "yes"]', style: { "border-width": 4, "border-style": "double", "border-color": c.accent } },
    {
      selector: 'node[type = "concept"]',
      style: { shape: "round-rectangle", "background-color": c.surface, "border-color": c.topic, "border-style": "dotted", width: 22, height: 16 },
    },
    {
      selector: 'node[type = "scholar"], node[type = "author"]',
      style: { shape: "diamond", "background-color": c.authored, "border-color": c.authored, width: 18, height: 18 },
    },
    { selector: 'node[type = "project"]', style: { shape: "hexagon", "background-color": c.accent, "border-color": c.accent } },
    // Wheel layout: labels point away from the paper in the middle.
    { selector: 'node[side = "right"]', style: { "text-halign": "right", "text-valign": "center", "text-margin-x": 7, "text-margin-y": 0 } },
    { selector: 'node[side = "left"]', style: { "text-halign": "left", "text-valign": "center", "text-margin-x": -7, "text-margin-y": 0 } },
    {
      selector: "node.header",
      style: {
        width: 1,
        height: 1,
        "background-opacity": 0,
        "border-width": 0,
        color: c.faint,
        "font-size": 9.5,
        "font-weight": 600,
        "text-transform": "uppercase",
        events: "no",
      },
    },
    // A header lines up with the dots of the rows below it.
    { selector: 'node.header[side = "right"]', style: { "text-margin-x": -6 } },
    { selector: 'node.header[side = "left"]', style: { "text-margin-x": 6 } },
    {
      selector: 'node[center = "yes"]',
      style: {
        "border-width": 4,
        "border-color": c.accent,
        color: c.text,
        "font-size": 12,
        "font-weight": 600,
        "text-wrap": "wrap",
        "text-max-width": "260px",
        "text-margin-y": 7,
        "text-background-color": c.bg,
        "text-background-opacity": 0.85,
        "text-background-padding": "4px",
        "text-background-shape": "roundrectangle",
      },
    },
    {
      selector: "node:selected, node.hovered",
      style: {
        label: "data(long)",
        "text-max-width": "360px",
        "border-color": c.text,
        "border-width": 3,
        color: c.text,
        "text-background-color": c.bg,
        "text-background-opacity": 0.92,
        "text-background-padding": "3px",
        "text-background-shape": "roundrectangle",
        "z-index": 10,
      },
    },
    {
      selector: "edge",
      style: {
        width: 1.5,
        "curve-style": "bezier",
        "line-color": c.cites,
        opacity: 0.7,
        "overlay-opacity": 0,
        "transition-property": "opacity",
        "transition-duration": 200,
      },
    },
    {
      selector: 'edge[relation = "CITES"]',
      style: { "line-style": "solid", "target-arrow-shape": "triangle", "target-arrow-color": c.cites, "arrow-scale": 0.9 },
    },
    { selector: 'edge[relation = "SEMANTICALLY_SIMILAR"]', style: { "line-style": "dashed", "line-dash-pattern": [6, 4], "line-color": c.similar } },
    { selector: 'edge[relation = "HAS_TOPIC"]', style: { "line-style": "dotted", "line-color": c.topic, width: 1.2, opacity: 0.55 } },
    { selector: 'edge[relation = "AUTHORED"]', style: { "line-color": c.authored, width: 1, opacity: 0.6 } },
    { selector: "edge:selected, edge.hovered", style: { width: 3, opacity: 1, "z-index": 10 } },
    { selector: ".faded", style: { opacity: 0.12 } },
    { selector: ".hidden", style: { display: "none" } },
  ];
}
