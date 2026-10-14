/**
 * Controllable `window.matchMedia` for jsdom (installed by setup.ts; reset after every test).
 *
 *   setMatchMedia({ "(prefers-reduced-motion: reduce)": true });
 *   setMatchMedia((query) => query.includes("light"));
 *
 * Existing MediaQueryLists fire "change" when their result flips.
 */
type Matcher = (query: string) => boolean;

const defaultMatcher: Matcher = () => false;
let matcher: Matcher = defaultMatcher;
const lists = new Set<FakeMediaQueryList>();

class FakeMediaQueryList extends EventTarget {
  readonly media: string;
  onchange: ((event: Event) => void) | null = null;
  private last: boolean;

  constructor(media: string) {
    super();
    this.media = media;
    this.last = matcher(media);
  }

  get matches(): boolean {
    return matcher(this.media);
  }

  /** Deprecated MediaQueryList API, still used by some libraries. */
  addListener(listener: ((event: Event) => void) | null): void {
    if (listener) this.addEventListener("change", listener);
  }

  removeListener(listener: ((event: Event) => void) | null): void {
    if (listener) this.removeEventListener("change", listener);
  }

  sync(): void {
    const matches = this.matches;
    if (matches === this.last) return;
    this.last = matches;
    const event = Object.assign(new Event("change"), { matches, media: this.media });
    this.onchange?.(event);
    this.dispatchEvent(event);
  }
}

/** Set which media queries match (function or exact-query map). */
export function setMatchMedia(next: Matcher | Readonly<Record<string, boolean>>): void {
  matcher = typeof next === "function" ? next : (query) => next[query] ?? false;
  for (const list of lists) list.sync();
}

export function installMatchMedia(): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string): MediaQueryList => {
      const list = new FakeMediaQueryList(query);
      lists.add(list);
      return list as unknown as MediaQueryList;
    },
  });
}

export function resetMatchMedia(): void {
  matcher = defaultMatcher;
  lists.clear();
}
