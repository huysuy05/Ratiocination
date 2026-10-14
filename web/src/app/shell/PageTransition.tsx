import { motion } from "motion/react";
import type { ReactNode } from "react";
import { useMatch, useMatches } from "react-router";
import { useReducedMotionPreference } from "../../components/ui/RadarSweep";
import { pageTransition } from "../../lib/motion";
import { ROUTE_PATTERNS } from "../paths";

/**
 * Page transition (UI.md §3: fade + 8 px rise; fade only when reduced). Keyed by the matched route
 * and project, so a page re-enters when the route or project changes but stays mounted when only
 * its params/search change (e.g. the graph re-centring on /graph/:paperId, `?paper=` sheets).
 * Enter-only: the previous page leaves immediately, so navigation never waits for an exit.
 */
export function PageTransition({ children }: { children: ReactNode }) {
  const matches = useMatches();
  const leaf = matches[matches.length - 1];
  const projectId = useMatch(ROUTE_PATTERNS.projectScope)?.params.projectId ?? "";
  const reduced = useReducedMotionPreference();
  const transition = pageTransition(reduced);
  return (
    <motion.div
      key={`${leaf?.id ?? "root"}:${projectId}`}
      initial={transition.initial}
      animate={transition.animate}
      transition={transition.transition}
      className="w-full flex-1"
    >
      {children}
    </motion.div>
  );
}
