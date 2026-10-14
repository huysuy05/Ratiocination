/** Small DOM hooks for the landing page: visibility (to pause loops) and fit-to-width scaling. */
import { useEffect, useLayoutEffect, useState, useSyncExternalStore, type RefObject } from "react";

/**
 * Whether `ref` is on screen (IntersectionObserver). Starts `true` so nothing waits on the observer;
 * the first callback corrects it right away. Used to pause infinite loops while off-screen.
 */
export function useOnScreen(ref: RefObject<Element | null>, rootMargin = "0px"): boolean {
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[entries.length - 1];
        if (entry) setOnScreen(entry.isIntersecting);
      },
      { rootMargin },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, rootMargin]);
  return onScreen;
}

function subscribeVisibility(listener: () => void): () => void {
  document.addEventListener("visibilitychange", listener);
  return () => document.removeEventListener("visibilitychange", listener);
}

/** False while the tab is hidden. */
export function usePageVisible(): boolean {
  return useSyncExternalStore(
    subscribeVisibility,
    () => document.visibilityState !== "hidden",
    () => true,
  );
}

/**
 * Scales a fixed-size design (`designWidth` px wide) to the width of `ref`: writes `--fit`
 * (≤ 1) on the element, for children drawn with `transform: scale(var(--fit))`.
 */
export function useFitScale(ref: RefObject<HTMLElement | null>, designWidth: number): void {
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const update = () => {
      const width = element.clientWidth;
      if (width > 0) element.style.setProperty("--fit", String(Math.min(1, width / designWidth)));
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, designWidth]);
}
