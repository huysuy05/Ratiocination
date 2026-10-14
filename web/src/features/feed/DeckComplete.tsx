/** End of the deck (UI.md §7): a radar pulse, today's decisions by state — finite by design. */
import { motion } from "motion/react";
import { Plus, Waypoints } from "lucide-react";
import { forwardRef } from "react";
import { Link } from "react-router";
import type { RecommendationItem } from "../../api/types";
import { paths } from "../../app/paths";
import { Button } from "../../components/ui/Button";
import { RadarScope } from "../../components/ui/RadarSweep";
import { PAPER_STATES, STATE_META } from "../../lib/states";

export interface DeckCompleteProps {
  projectId: number;
  items: readonly RecommendationItem[];
  onMore: () => void;
  busy: boolean;
}

export const DeckComplete = forwardRef<HTMLHeadingElement, DeckCompleteProps>(function DeckComplete({ projectId, items, onMore, busy }, ref) {
  const counts = PAPER_STATES.map((state) => ({ state, n: items.filter((item) => item.state === state).length }));
  const firstLiked = items.find((item) => item.state === "relevant" || item.state === "saved");
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.3 }}
      className="mx-auto flex w-full max-w-[560px] flex-col items-center gap-6 rounded-card border border-border bg-surface px-6 py-10 text-center surface-sheen elevation-card"
    >
      <div className="relative">
        <span aria-hidden="true" className="absolute inset-0 rounded-full animate-radar-pulse" />
        <RadarScope size="sm" animated={false} />
      </div>
      <div className="flex flex-col gap-2">
        <h2 ref={ref} tabIndex={-1} className="font-serif text-[26px] font-semibold tracking-[-0.015em] text-text outline-none">
          Deck complete
        </h2>
        <p className="text-[14.5px] text-muted">You went through all {items.length} papers. New ones arrive every morning.</p>
      </div>
      <ul className="flex flex-wrap justify-center gap-2">
        {counts.map(({ state, n }) => {
          const meta = STATE_META[state];
          const Icon = meta.icon;
          return (
            <li key={state} className="flex items-center gap-2 rounded-pill border border-border bg-surface-2 px-3.5 py-1.5 text-[13.5px]">
              <Icon aria-hidden="true" className="size-4" style={{ color: meta.color }} />
              <span className="font-semibold text-text tabular-nums">{n}</span>
              <span className="text-muted">{meta.label.toLowerCase()}</span>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap justify-center gap-2">
        {firstLiked ? (
          <Button asChild variant="primary" leadingIcon={<Waypoints aria-hidden="true" />}>
            <Link to={paths.graph(projectId, firstLiked.paper.id)}>Explore connections</Link>
          </Button>
        ) : null}
        <Button leadingIcon={<Plus aria-hidden="true" />} onClick={onMore} loading={busy}>
          More papers
        </Button>
      </div>
      <p className="text-[12.5px] text-faint">Come back tomorrow — a short daily deck beats an endless feed.</p>
    </motion.div>
  );
});
