import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { describe, expect, it, vi } from "vitest";
import { getCytoscapeInstances, lastCytoscapeInstance } from "./cytoscape";
import { setMatchMedia } from "./matchMedia";

describe("jsdom polyfills", () => {
  it("provides matchMedia with controllable results and change events", () => {
    const list = window.matchMedia("(prefers-reduced-motion: reduce)");
    expect(list.matches).toBe(false);
    const onChange = vi.fn();
    list.addEventListener("change", onChange);
    setMatchMedia({ "(prefers-reduced-motion: reduce)": true });
    expect(list.matches).toBe(true);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("provides observers, pointer capture, scrollIntoView and object URLs", () => {
    expect(() => new ResizeObserver(() => undefined).observe(document.body)).not.toThrow();
    expect(() => new IntersectionObserver(() => undefined).observe(document.body)).not.toThrow();
    const element = document.createElement("div");
    expect(element.hasPointerCapture(1)).toBe(false);
    expect(() => element.setPointerCapture(1)).not.toThrow();
    expect(() => element.releasePointerCapture(1)).not.toThrow();
    expect(() => element.scrollIntoView()).not.toThrow();
    expect(typeof URL.createObjectURL(new Blob(["x"]))).toBe("string");
  });

  it("resets storage and html attributes between tests (part 1)", () => {
    localStorage.setItem("rr.leak", "1");
    document.documentElement.setAttribute("data-theme", "light");
  });

  it("resets storage and html attributes between tests (part 2)", () => {
    expect(localStorage.getItem("rr.leak")).toBeNull();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});

describe("cytoscape mock", () => {
  it("records instances and keeps elements in memory", () => {
    cytoscape.use(fcose);
    const container = document.createElement("div");
    const cy = cytoscape({
      container,
      elements: [
        { group: "nodes", data: { id: "work:1", type: "work" } },
        { group: "nodes", data: { id: "concept:5", type: "concept" } },
      ],
    });
    expect(getCytoscapeInstances()).toHaveLength(1);
    const fake = lastCytoscapeInstance();
    expect(fake).toBe(cy);

    cy.add([{ group: "edges", data: { id: "e:1", source: "work:1", target: "concept:5" } }]);
    expect(cy.nodes().length).toBe(2);
    expect(cy.edges().length).toBe(1);
    expect(cy.getElementById("work:1").data("type")).toBe("work");
    expect(cy.$("node[type = 'concept']").length).toBe(1);
    expect(cy.getElementById("e:1").source().id()).toBe("work:1");

    cy.getElementById("work:1").addClass("center");
    expect(cy.$(".center").length).toBe(1);

    const layout = cy.layout({ name: "grid", animate: false });
    layout.run();
    expect(fake?.layouts).toEqual([{ name: "grid", animate: false }]);

    cy.getElementById("concept:5").remove();
    expect(cy.nodes().length).toBe(1);

    cy.destroy();
    expect(cy.destroyed()).toBe(true);
  });

  it("runs registered handlers through emitEvent and tolerates unknown methods", () => {
    const cy = cytoscape({ elements: [{ data: { id: "work:2" } }] });
    const onTap = vi.fn();
    cy.on("tap", "node", onTap);
    lastCytoscapeInstance()?.emitEvent("tap", "work:2");
    expect(onTap).toHaveBeenCalledTimes(1);
    expect(onTap.mock.calls[0]?.[0].target.id()).toBe("work:2");
    // Methods the fake does not model are chainable no-ops.
    expect(() => cy.userZoomingEnabled(false).boxSelectionEnabled(false)).not.toThrow();
  });

  it("starts each test with no instances", () => {
    expect(getCytoscapeInstances()).toHaveLength(0);
  });
});
