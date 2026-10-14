/**
 * Reusable Cytoscape mock (jsdom has no canvas). setup.ts registers it for every test file:
 *
 *   vi.mock("cytoscape", async () => (await import("./cytoscape")).createCytoscapeModule());
 *
 * so any `import cytoscape from "cytoscape"` gets a fake that keeps elements in memory. Inspect it
 * from a test:
 *
 *   const cy = lastCytoscapeInstance();          // the latest cytoscape({...}) result
 *   expect(cy?.add).toHaveBeenCalled();
 *   expect(cy?.getElementById("work:1").length).toBe(1);
 *   cy?.emitEvent("tap", "work:1");              // run handlers registered with cy.on("tap", …)
 *
 * Supported: add/remove/getElementById/$id/$/elements/nodes/edges (selectors: "node", "edge",
 * "#id", ".class", "[key = 'value']"), on/one/off/emit, layout().run(), batch, fit/center/zoom/
 * pan/resize/destroy, style(); collections support forEach/map/filter/data/position/classes/
 * remove/source/target/connectedEdges and more. Any other method is a chainable no-op `vi.fn`.
 * Opt out in one file with `vi.unmock("cytoscape")`.
 */
import { vi, type Mock } from "vitest";

type Group = "nodes" | "edges";
type Position = { x: number; y: number };

export interface FakeElement {
  id: string;
  group: Group;
  data: Record<string, unknown>;
  position: Position;
  classes: Set<string>;
}

export interface FakeEvent {
  type: string;
  target: FakeCollection | FakeCore;
  cy: FakeCore;
  originalEvent?: unknown;
  [key: string]: unknown;
}

type Handler = (event: FakeEvent) => void;

interface Listener {
  events: string[];
  selector: string | null;
  handler: Handler;
  once: boolean;
}

/** A collection (array-like). Unknown methods are chainable no-ops. */
export interface FakeCollection extends Iterable<FakeCollection> {
  readonly length: number;
  readonly elements: FakeElement[];
  [method: string]: unknown;
  size(): number;
  empty(): boolean;
  nonempty(): boolean;
  id(): string | undefined;
  data(key?: string | Record<string, unknown>, value?: unknown): unknown;
  position(next?: Position | string, value?: number): unknown;
  forEach(fn: (element: FakeCollection, index: number) => void): FakeCollection;
  map<T>(fn: (element: FakeCollection, index: number) => T): T[];
  filter(selectorOrFn: string | ((element: FakeCollection, index: number) => boolean)): FakeCollection;
  toArray(): FakeCollection[];
  first(): FakeCollection;
  last(): FakeCollection;
  remove(): FakeCollection;
  hasClass(name: string): boolean;
  isNode(): boolean;
  isEdge(): boolean;
}

export interface FakeCore {
  [method: string]: unknown;
  readonly options: Record<string, unknown>;
  readonly store: Map<string, FakeElement>;
  readonly layouts: Record<string, unknown>[];
  add: Mock<(definitions: unknown) => FakeCollection>;
  remove: Mock<(target: unknown) => FakeCollection>;
  getElementById: Mock<(id: string) => FakeCollection>;
  $id: Mock<(id: string) => FakeCollection>;
  $: Mock<(selector?: string) => FakeCollection>;
  elements: Mock<(selector?: string) => FakeCollection>;
  nodes: Mock<(selector?: string) => FakeCollection>;
  edges: Mock<(selector?: string) => FakeCollection>;
  on: Mock<(...args: unknown[]) => FakeCore>;
  one: Mock<(...args: unknown[]) => FakeCore>;
  off: Mock<(...args: unknown[]) => FakeCore>;
  layout: Mock<(options: Record<string, unknown>) => FakeLayout>;
  destroy: Mock<() => void>;
  destroyed: () => boolean;
  /** Test helper: run handlers for `type` (optionally on the element with `targetId`). */
  emitEvent: (type: string, targetId?: string, extra?: Record<string, unknown>) => void;
}

export interface FakeLayout {
  options: Record<string, unknown>;
  run: Mock<() => FakeLayout>;
  stop: Mock<() => FakeLayout>;
  on: Mock<(...args: unknown[]) => FakeLayout>;
  one: Mock<(...args: unknown[]) => FakeLayout>;
  promiseOn: Mock<(...args: unknown[]) => Promise<void>>;
}

const instances: FakeCore[] = [];

/** Every cytoscape() instance created in the current test. */
export function getCytoscapeInstances(): readonly FakeCore[] {
  return instances;
}

export function lastCytoscapeInstance(): FakeCore | undefined {
  return instances[instances.length - 1];
}

/** Called by setup.ts after each test. */
export function resetCytoscapeMock(): void {
  instances.length = 0;
}

/** Wrap an object so unknown string properties become chainable no-op mocks returning `self`. */
function chainable<T extends object>(target: T, selfRef: () => unknown): T {
  const extras = new Map<string, Mock>();
  return new Proxy(target, {
    get(obj, prop, receiver) {
      if (typeof prop === "symbol" || prop in obj) return Reflect.get(obj, prop, receiver) as unknown;
      if (prop === "then" || prop === "toJSON" || prop === "$$typeof") return undefined;
      let fn = extras.get(prop);
      if (!fn) {
        fn = vi.fn(() => selfRef());
        extras.set(prop, fn);
      }
      return fn;
    },
  });
}

function parseSelector(selector: string | undefined): (element: FakeElement) => boolean {
  const s = (selector ?? "").trim();
  if (!s || s === "*") return () => true;
  if (s === "node" || s === "nodes") return (e) => e.group === "nodes";
  if (s === "edge" || s === "edges") return (e) => e.group === "edges";
  if (s.startsWith("#")) {
    const id = s.slice(1).replace(/\\/g, "");
    return (e) => e.id === id;
  }
  if (s.startsWith(".")) {
    const name = s.slice(1);
    return (e) => e.classes.has(name);
  }
  const attr = /^(node|edge)?\s*\[\s*([\w-]+)\s*=\s*['"]?([^'"\]]*)['"]?\s*\]$/.exec(s);
  if (attr) {
    const [, kind, key = "", value = ""] = attr;
    return (e) =>
      (!kind || e.group === (kind === "node" ? "nodes" : "edges")) && String(e.data[key] ?? (key === "id" ? e.id : "")) === value;
  }
  return () => true;
}

/** Create a fake core (what `cytoscape(options)` returns). */
export function createFakeCore(options: Record<string, unknown> = {}): FakeCore {
  const store = new Map<string, FakeElement>();
  const listeners: Listener[] = [];
  const layouts: Record<string, unknown>[] = [];
  let destroyed = false;
  let autoId = 0;
  let zoom = 1;
  let pan: Position = { x: 0, y: 0 };
  // Assigned below; collections and listeners refer to it lazily.
  let core: FakeCore;

  const live = (): FakeElement[] => [...store.values()];

  function collection(elements: FakeElement[]): FakeCollection {
    const items = [...elements];
    const self: FakeCollection = chainable(
      {
        get length() {
          return items.length;
        },
        get elements() {
          return items;
        },
        [Symbol.iterator]: function* () {
          for (const element of items) yield collection([element]);
        },
        size: () => items.length,
        empty: () => items.length === 0,
        nonempty: () => items.length > 0,
        id: () => items[0]?.id,
        data(key?: string | Record<string, unknown>, value?: unknown): unknown {
          if (key === undefined) return items[0] ? { ...items[0].data } : undefined;
          if (typeof key === "object") {
            for (const element of items) Object.assign(element.data, key);
            return self;
          }
          if (value === undefined) return items[0]?.data[key];
          for (const element of items) element.data[key] = value;
          return self;
        },
        position(next?: Position | string, value?: number): unknown {
          if (next === undefined) return items[0] ? { ...items[0].position } : undefined;
          if (typeof next === "string") {
            if (value === undefined) return items[0]?.position[next as keyof Position];
            for (const element of items) element.position[next as keyof Position] = value;
            return self;
          }
          for (const element of items) element.position = { ...next };
          return self;
        },
        forEach(fn: (element: FakeCollection, index: number) => void) {
          items.forEach((element, index) => fn(collection([element]), index));
          return self;
        },
        map<T>(fn: (element: FakeCollection, index: number) => T): T[] {
          return items.map((element, index) => fn(collection([element]), index));
        },
        filter(selectorOrFn: string | ((element: FakeCollection, index: number) => boolean)) {
          if (typeof selectorOrFn === "string") return collection(items.filter(parseSelector(selectorOrFn)));
          return collection(items.filter((element, index) => selectorOrFn(collection([element]), index)));
        },
        toArray: () => items.map((element) => collection([element])),
        first: () => collection(items.slice(0, 1)),
        last: () => collection(items.slice(-1)),
        eq: (index: number) => collection(items.slice(index, index + 1)),
        remove() {
          for (const element of items) store.delete(element.id);
          return self;
        },
        addClass(names: string) {
          for (const element of items) for (const name of names.split(/\s+/).filter(Boolean)) element.classes.add(name);
          return self;
        },
        removeClass(names: string) {
          for (const element of items) for (const name of names.split(/\s+/).filter(Boolean)) element.classes.delete(name);
          return self;
        },
        toggleClass(name: string, on?: boolean) {
          for (const element of items) {
            const enable = on ?? !element.classes.has(name);
            if (enable) element.classes.add(name);
            else element.classes.delete(name);
          }
          return self;
        },
        hasClass: (name: string) => items[0]?.classes.has(name) ?? false,
        classes: () => [...(items[0]?.classes ?? [])],
        isNode: () => items[0]?.group === "nodes",
        isEdge: () => items[0]?.group === "edges",
        source: () => collection(items.flatMap((e) => (store.get(String(e.data.source)) ? [store.get(String(e.data.source)) as FakeElement] : []))),
        target: () => collection(items.flatMap((e) => (store.get(String(e.data.target)) ? [store.get(String(e.data.target)) as FakeElement] : []))),
        connectedEdges: () => {
          const ids = new Set(items.map((e) => e.id));
          return collection(live().filter((e) => e.group === "edges" && (ids.has(String(e.data.source)) || ids.has(String(e.data.target)))));
        },
        union: (other: FakeCollection) => collection([...new Set([...items, ...other.elements])]),
        degree: () => {
          const id = items[0]?.id;
          return live().filter((e) => e.group === "edges" && (e.data.source === id || e.data.target === id)).length;
        },
        boundingBox: () => ({ x1: 0, y1: 0, x2: 100, y2: 100, w: 100, h: 100 }),
        layout: (layoutOptions: Record<string, unknown>) => core.layout(layoutOptions),
      },
      () => self,
    ) as FakeCollection;
    return self;
  }

  function addOne(definition: unknown): FakeElement | null {
    if (typeof definition !== "object" || definition === null) return null;
    const def = definition as { group?: Group; data?: Record<string, unknown>; position?: Position; classes?: string | string[] };
    const data = { ...(def.data ?? {}) };
    const group: Group = def.group ?? (data.source !== undefined && data.target !== undefined ? "edges" : "nodes");
    const id = typeof data.id === "string" && data.id ? data.id : `auto-${++autoId}`;
    data.id = id;
    const classes = new Set(
      (Array.isArray(def.classes) ? def.classes : (def.classes ?? "").split(/\s+/)).filter((name): name is string => Boolean(name)),
    );
    const element: FakeElement = { id, group, data, position: { ...(def.position ?? { x: 0, y: 0 }) }, classes };
    store.set(id, element);
    return element;
  }

  function addDefinitions(definitions: unknown): FakeElement[] {
    if (definitions === null || definitions === undefined) return [];
    if (Array.isArray(definitions)) return definitions.flatMap((def) => addOne(def) ?? []);
    if (typeof definitions === "object") {
      const grouped = definitions as { nodes?: unknown[]; edges?: unknown[]; elements?: FakeElement[] };
      if (grouped.elements) return grouped.elements.flatMap((e) => addOne({ group: e.group, data: e.data, position: e.position }) ?? []);
      if (grouped.nodes || grouped.edges) {
        return [
          ...(grouped.nodes ?? []).flatMap((def) => addOne({ group: "nodes", ...(def as object) }) ?? []),
          ...(grouped.edges ?? []).flatMap((def) => addOne({ group: "edges", ...(def as object) }) ?? []),
        ];
      }
    }
    const single = addOne(definitions);
    return single ? [single] : [];
  }

  function register(once: boolean, args: unknown[]): FakeCore {
    const [events, second, third] = args;
    const handler = (typeof second === "function" ? second : third) as Handler | undefined;
    const selector = typeof second === "string" ? second : null;
    if (typeof events === "string" && handler) {
      listeners.push({ events: events.split(/\s+/).filter(Boolean), selector, handler, once });
    }
    return core;
  }

  const base = {
    options,
    store,
    layouts,
    add: vi.fn((definitions: unknown) => collection(addDefinitions(definitions))),
    remove: vi.fn((target: unknown) => {
      const removed =
        typeof target === "string"
          ? live().filter(parseSelector(target))
          : target && typeof target === "object" && "elements" in target
            ? (target as FakeCollection).elements
            : [];
      for (const element of removed) store.delete(element.id);
      return collection(removed);
    }),
    getElementById: vi.fn((id: string) => collection(store.has(id) ? [store.get(id) as FakeElement] : [])),
    $id: vi.fn((id: string) => collection(store.has(id) ? [store.get(id) as FakeElement] : [])),
    $: vi.fn((selector?: string) => collection(live().filter(parseSelector(selector)))),
    elements: vi.fn((selector?: string) => collection(live().filter(parseSelector(selector)))),
    nodes: vi.fn((selector?: string) => collection(live().filter((e) => e.group === "nodes").filter(parseSelector(selector)))),
    edges: vi.fn((selector?: string) => collection(live().filter((e) => e.group === "edges").filter(parseSelector(selector)))),
    on: vi.fn((...args: unknown[]) => register(false, args)),
    one: vi.fn((...args: unknown[]) => register(true, args)),
    once: vi.fn((...args: unknown[]) => register(true, args)),
    off: vi.fn((...args: unknown[]) => {
      const [events, second, third] = args;
      const handler = typeof second === "function" ? second : third;
      if (typeof events !== "string") listeners.length = 0;
      else {
        const names = events.split(/\s+/);
        for (let i = listeners.length - 1; i >= 0; i -= 1) {
          const listener = listeners[i];
          if (listener && listener.events.some((name) => names.includes(name)) && (!handler || listener.handler === handler)) {
            listeners.splice(i, 1);
          }
        }
      }
      return core;
    }),
    emit: vi.fn(() => core),
    trigger: vi.fn(() => core),
    layout: vi.fn((layoutOptions: Record<string, unknown>) => {
      layouts.push(layoutOptions);
      const layout: FakeLayout = {
        options: layoutOptions,
        run: vi.fn(() => layout),
        stop: vi.fn(() => layout),
        on: vi.fn(() => layout),
        one: vi.fn(() => layout),
        promiseOn: vi.fn(() => Promise.resolve()),
      };
      return layout;
    }),
    batch: vi.fn((fn?: () => void) => {
      fn?.();
      return core;
    }),
    startBatch: vi.fn(() => core),
    endBatch: vi.fn(() => core),
    zoom: vi.fn((level?: number | { level: number }) => {
      if (level === undefined) return zoom;
      zoom = typeof level === "number" ? level : level.level;
      return core;
    }),
    pan: vi.fn((next?: Position) => {
      if (next === undefined) return { ...pan };
      pan = { ...next };
      return core;
    }),
    width: () => 800,
    height: () => 600,
    extent: () => ({ x1: 0, y1: 0, x2: 800, y2: 600, w: 800, h: 600 }),
    container: () => (options.container as Element | undefined) ?? null,
    ready: vi.fn((fn?: () => void) => {
      fn?.();
      return core;
    }),
    destroy: vi.fn(() => {
      destroyed = true;
      store.clear();
      listeners.length = 0;
    }),
    destroyed: () => destroyed,
    json: vi.fn(() => ({ elements: live().map((e) => ({ group: e.group, data: { ...e.data }, position: { ...e.position } })) })),
    emitEvent(type: string, targetId?: string, extra: Record<string, unknown> = {}) {
      const element = targetId ? store.get(targetId) : undefined;
      const target = element ? collection([element]) : core;
      for (const listener of [...listeners]) {
        if (!listener.events.includes(type)) continue;
        if (listener.selector) {
          if (!element || !parseSelector(listener.selector)(element)) continue;
        }
        if (listener.once) listeners.splice(listeners.indexOf(listener), 1);
        listener.handler({ type, target, cy: core, ...extra });
      }
    },
  };

  core = chainable(base, () => core) as unknown as FakeCore;
  if (options.elements) addDefinitions(options.elements);
  return core;
}

/** Module shape for `vi.mock("cytoscape", …)`: a callable default export with `use()`. */
export function createCytoscapeModule() {
  const cytoscape = Object.assign(
    vi.fn((options?: Record<string, unknown>) => {
      const core = createFakeCore(options ?? {});
      instances.push(core);
      return core;
    }),
    { use: vi.fn(), warnings: vi.fn(), version: "mock" },
  );
  return { default: cytoscape };
}
