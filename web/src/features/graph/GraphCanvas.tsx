/**
 * The knowledge-graph canvas (UI.md §9): one Cytoscape instance per mount. Elements are diffed in
 * and out, so an expansion grows the graph instead of redrawing it. A paper's neighbourhood
 * (`center` set) is laid out as a wheel around the paper (./layout), new nodes coming out of the
 * middle; the project graph uses fcose, new nodes starting next to a node they link to. Both animate
 * unless reduced motion. The canvas is `aria-hidden` — the list view is its accessible equivalent.
 */
import cytoscape, { type Core, type ElementDefinition, type EventObject, type Position } from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";
import type { GraphEdge, GraphGroup, GraphNode, Relation } from "../../api/types";
import { useMotionPreference } from "../../lib/motion";
import { useTheme } from "../../lib/theme";
import { radialLayout } from "./layout";
import { edgeData, graphStylesheet, nodeData, readGraphColors } from "./style";

let fcoseRegistered = false;

function registerFcose(): void {
  if (fcoseRegistered) return;
  cytoscape.use(fcose);
  fcoseRegistered = true;
}

const NO_GROUPS: readonly GraphGroup[] = [];
const MIN_ZOOM = 0.15;
const MAX_ZOOM = 3;
/** Fitting a small graph stops here, so a handful of nodes does not get giant labels. */
const MAX_FIT_ZOOM = 1.25;

/** Offsets on a golden-angle spiral, so nodes added together never start on one spot. */
function spiral(index: number): Position {
  const angle = index * 2.399963;
  const radius = 28 + 10 * Math.sqrt(index);
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}

function centroid(cy: Core): Position {
  let x = 0;
  let y = 0;
  let count = 0;
  cy.nodes().forEach((node) => {
    if (node.hasClass("header")) return;
    const position = node.position();
    x += position.x;
    y += position.y;
    count += 1;
  });
  return count > 0 ? { x: x / count, y: y / count } : { x: 0, y: 0 };
}

export interface GraphCanvasProps {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  /** The paper in the middle of a neighbourhood: lays the graph out as a wheel around it. */
  center?: string | null;
  /** The neighbourhood's groups: one header and run of rows each in the wheel. */
  groups?: readonly GraphGroup[];
  hiddenRelations: ReadonlySet<Relation>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Called with the node id on double click (e.g. centre the graph on a work). */
  onActivate?: (id: string) => void;
}

export function GraphCanvas({ nodes, edges, center = null, groups = NO_GROUPS, hiddenRelations, selectedId, onSelect, onActivate }: GraphCanvasProps) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const firstLayout = useRef(true);
  const handlers = useRef({ onSelect, onActivate });
  const { reduced } = useMotionPreference();
  const { resolved } = useTheme();

  useEffect(() => {
    handlers.current = { onSelect, onActivate };
  });

  // Create the instance once (twice in development: StrictMode mounts effects twice, and the second
  // instance starts empty, so it needs a first layout of its own).
  useEffect(() => {
    registerFcose();
    const cy = cytoscape({
      container: container.current,
      elements: [],
      style: graphStylesheet(readGraphColors()),
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      boxSelectionEnabled: false,
      selectionType: "single",
    });
    cy.on("tap", "node, edge", (event: EventObject) => handlers.current.onSelect(event.target.id()));
    cy.on("tap", (event: EventObject) => {
      if (event.target === cy) handlers.current.onSelect(null);
    });
    cy.on("dbltap", "node", (event: EventObject) => handlers.current.onActivate?.(event.target.id()));
    cy.on("mouseover", "node, edge", (event: EventObject) => {
      event.target.addClass("hovered");
      if (container.current) container.current.style.cursor = "pointer";
    });
    cy.on("mouseout", "node, edge", (event: EventObject) => {
      event.target.removeClass("hovered");
      if (container.current) container.current.style.cursor = "";
    });
    cyRef.current = cy;
    firstLayout.current = true;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // Restyle when the theme changes (Cytoscape cannot read CSS variables).
  useEffect(() => {
    cyRef.current?.style(graphStylesheet(readGraphColors()));
  }, [resolved]);

  // Sync elements; lay out only when the structure changed.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const nodeIds = new Set(nodes.map((node) => node.id));
    const validEdges = edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
    const edgeIds = new Set(validEdges.map((edge) => edge.id));
    const wheel = center !== null && nodeIds.has(center) ? radialLayout(nodes, validEdges, groups, center) : null;
    const headers = new Map((wheel?.headers ?? []).map((header) => [header.id, header]));
    let changed = false;
    let added = 0;
    cy.batch(() => {
      cy.elements().forEach((element) => {
        const id: string = element.id();
        const keep = element.isNode() ? nodeIds.has(id) || headers.has(id) : edgeIds.has(id);
        if (!keep) {
          element.remove();
          changed = true;
        }
      });
      // New nodes come out of the paper in the middle, or start next to a node they link to.
      const middle = wheel && center !== null ? cy.getElementById(center) : null;
      const origin = middle && middle.nonempty() ? { ...middle.position() } : centroid(cy);
      const definitions: ElementDefinition[] = [];
      for (const node of nodes) {
        const data = { ...nodeData(node, wheel !== null), side: wheel?.sides.get(node.id) ?? "below" };
        const existing = cy.getElementById(node.id);
        if (existing.nonempty()) {
          existing.data(data);
          continue;
        }
        let position = origin;
        if (!wheel) {
          const link = validEdges.find((edge) => edge.source === node.id || edge.target === node.id);
          const anchor = link ? cy.getElementById(link.source === node.id ? link.target : link.source) : null;
          const base = anchor && anchor.nonempty() ? anchor.position() : origin;
          const offset = spiral(added);
          position = { x: base.x + offset.x, y: base.y + offset.y };
        }
        definitions.push({ group: "nodes", data, position: { ...position } });
        added += 1;
        changed = true;
      }
      for (const header of headers.values()) {
        const data = { id: header.id, type: "header", label: header.text, short: header.text, long: header.text, side: header.side };
        const existing = cy.getElementById(header.id);
        if (existing.nonempty()) {
          existing.data(data);
          continue;
        }
        // Headers travel with their rows, so the wheel unfolds as one picture.
        definitions.push({ group: "nodes", data, classes: "header", position: { ...origin }, selectable: false, grabbable: false });
        changed = true;
      }
      for (const edge of validEdges) {
        if (cy.getElementById(edge.id).empty()) {
          definitions.push({ group: "edges", data: edgeData(edge) });
          changed = true;
        }
      }
      if (definitions.length > 0) cy.add(definitions);
    });
    if (!changed || nodes.length === 0) return;
    const first = firstLayout.current;
    firstLayout.current = false;
    if (first) {
      // Grow out of the middle of the canvas, not out of its top-left corner.
      cy.zoom(1);
      cy.pan({ x: cy.width() / 2, y: cy.height() / 2 });
    }
    const motion = { animate: !reduced, animationDuration: 650, animationEasing: "ease-out-cubic", fit: true, padding: 40 } as const;
    let layout: cytoscape.LayoutOptions;
    if (wheel) {
      const positions: Record<string, Position> = Object.fromEntries(wheel.positions);
      for (const header of headers.values()) positions[header.id] = header.position;
      layout = { name: "preset", positions, ...motion };
    } else {
      layout = {
        name: "fcose",
        ...motion,
        // Start from scratch when most nodes are new: fcose cannot untangle nodes stacked on one spot.
        randomize: first || added * 2 > nodes.length,
        nodeDimensionsIncludeLabels: true,
        nodeRepulsion: () => 9000,
        idealEdgeLength: () => 110,
        nodeSeparation: 90,
        quality: "default",
      } as cytoscape.LayoutOptions;
    }
    // Both layouts compute the fitted viewport while `run()` runs, so the cap only applies to the fit.
    cy.maxZoom(MAX_FIT_ZOOM);
    cy.layout(layout).run();
    cy.maxZoom(MAX_ZOOM);
  }, [nodes, edges, center, groups, reduced]);

  // Relation filters.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.edges().forEach((edge) => {
      if (hiddenRelations.has(edge.data("relation") as Relation)) edge.addClass("hidden");
      else edge.removeClass("hidden");
    });
  }, [hiddenRelations, nodes, edges]);

  // Selection: highlight the selected element and its neighbourhood, fade the rest (not the headers).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("faded");
      cy.elements().unselect();
      if (!selectedId) return;
      const target = cy.getElementById(selectedId);
      if (target.empty()) return;
      target.select();
      const keep = target.isNode()
        ? cy.nodes().getElementById(selectedId).closedNeighborhood()
        : cy.edges().getElementById(selectedId).connectedNodes().union(cy.getElementById(selectedId));
      cy.elements().difference(keep).not(".header").addClass("faded");
    });
  }, [selectedId, nodes, edges]);

  return <div ref={container} aria-hidden="true" data-testid="graph-canvas" className="h-full w-full" />;
}
