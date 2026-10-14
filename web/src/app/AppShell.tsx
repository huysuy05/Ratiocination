/**
 * App shell (UI.md §4).
 *
 * Layout: ≥ 1024 px a 248 px sidebar (logo, project switcher, project + workspace nav, theme,
 * privacy badge); 768–1023 px an icon rail with tooltips; < 768 px a top bar (switcher, theme) and
 * bottom tabs (Feed, Library, Graph, Projects, More → full-screen sheet). Pages fade in with an
 * 8 px rise (fade only under reduced motion); a thin progress bar shows slow route loads.
 *
 * Behaviour contract:
 * - wraps the outlet in `<LayoutGroup id="paper">` (shared-element transitions, UI.md §5);
 * - writes `rr.lastProjectId` on every `/projects/:projectId/*` route.
 * Sign-in is enforced by `RequireAuth` around this shell.
 */
import { LayoutGroup } from "motion/react";
import { useEffect } from "react";
import { Outlet, useMatch } from "react-router";
import { parseId } from "../api/hooks";
import { TooltipProvider } from "../components/ui/Tooltip";
import { readLocalId, STORAGE_KEYS, writeLocal } from "../lib/storage";
import { ROUTE_PATTERNS } from "./paths";
import { BottomTabs, TopBar } from "./shell/MobileNav";
import { PageTransition } from "./shell/PageTransition";
import { RouteProgress } from "./shell/RouteProgress";
import { Sidebar } from "./shell/Sidebar";

export function AppShell() {
  const projectMatch = useMatch(ROUTE_PATTERNS.projectScope);
  const routeProjectId = parseId(projectMatch?.params.projectId);
  const navProjectId = routeProjectId ?? readLocalId(STORAGE_KEYS.lastProjectId);

  useEffect(() => {
    if (routeProjectId !== null) writeLocal(STORAGE_KEYS.lastProjectId, String(routeProjectId));
  }, [routeProjectId]);

  return (
    <TooltipProvider>
      <a
        href="#main"
        className="sr-only rounded-button bg-surface px-4 py-2.5 text-sm font-medium text-text elevation-popover focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[90]"
      >
        Skip to content
      </a>
      <RouteProgress />
      <div className="relative flex min-h-dvh bg-bg text-text">
        <Sidebar projectId={navProjectId} />
        <div className="relative isolate flex min-w-0 flex-1 flex-col">
          <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] app-backdrop" />
          <TopBar projectId={navProjectId} />
          <main
            id="main"
            tabIndex={-1}
            className="relative flex min-w-0 flex-1 flex-col px-4 pt-5 pb-28 outline-none sm:px-6 md:px-8 md:pt-8 md:pb-12 lg:px-10"
          >
            <LayoutGroup id="paper">
              <PageTransition>
                <Outlet />
              </PageTransition>
            </LayoutGroup>
          </main>
        </div>
      </div>
      <BottomTabs projectId={navProjectId} />
    </TooltipProvider>
  );
}
