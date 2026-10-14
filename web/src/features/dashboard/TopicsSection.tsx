/**
 * Topics you lean into (UI.md §12.3): rising and fading topics (share of your liked papers now vs
 * the previous period, with a 12-week sparkline), the top topics and the ones you keep skipping.
 */
import { motion } from "motion/react";
import { Compass, TrendingDown, TrendingUp } from "lucide-react";
import type { DashboardTopics, TopicStat } from "../../api/types";
import { Sparkline } from "../../components/charts/Sparkline";
import { Skeleton } from "../../components/ui/Skeleton";
import { useMotionPreference } from "../../lib/motion";
import { DeltaChip } from "./DeltaChip";
import { percent, periodPhrase, rateDelta } from "./format";
import { Panel } from "./Panel";

function TrendRow({ stat, rising }: { stat: TopicStat; rising: boolean }) {
  const delta = rateDelta(stat.share, stat.share_before);
  return (
    <li className="flex items-center gap-3 rounded-control px-2 py-2 hover:bg-surface-2/60">
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13.5px] font-medium text-text">{stat.label}</p>
        <p className="text-[12px] text-muted">
          {stat.count} liked now · {stat.count_before} before · {percent(stat.share)} of your likes
        </p>
      </div>
      <Sparkline values={stat.weekly} label={`${stat.label}: liked papers per week, last 12 weeks`} color={rising ? "var(--relevant)" : "var(--not-relevant)"} width={96} height={28} />
      <DeltaChip text={delta.text} tone={delta.tone} label="share change vs the previous period" />
    </li>
  );
}

function TopBars({ stats }: { stats: readonly TopicStat[] }) {
  const { reduced } = useMotionPreference();
  const max = Math.max(0.0001, ...stats.map((stat) => stat.share));
  return (
    <ol className="flex flex-col gap-2.5">
      {stats.map((stat, index) => (
        <li key={stat.key} className="flex flex-col gap-1">
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className="truncate text-text">{stat.label}</span>
            <span className="shrink-0 text-muted tabular-nums">
              {stat.count} · {percent(stat.share)}
            </span>
          </div>
          <span className="h-2 overflow-hidden rounded-pill bg-surface-3">
            <motion.span
              className="block h-full rounded-pill bg-gradient-to-r from-accent/70 to-accent"
              initial={reduced ? false : { width: 0 }}
              animate={{ width: `${(stat.share / max) * 100}%` }}
              transition={{ duration: 0.6, delay: reduced ? 0 : index * 0.05, ease: [0.22, 1, 0.36, 1] }}
            />
          </span>
        </li>
      ))}
    </ol>
  );
}

export function TopicsSection({ topics, loading }: { topics: DashboardTopics | undefined; loading: boolean }) {
  if (loading || !topics) return <Skeleton className="h-[360px] rounded-card" />;
  const span = periodPhrase(topics.period.days);
  if (topics.n_positive === 0 && topics.n_positive_before === 0) {
    return (
      <Panel title="Topics you lean into" icon={<Compass aria-hidden="true" />}>
        <p className="py-6 text-center text-[13.5px] text-muted">Mark a few papers relevant and your topics show up here.</p>
      </Panel>
    );
  }
  return (
    <Panel
      title="Topics you lean into"
      subtitle={`From the ${topics.n_positive} papers you kept ${span}, compared with the ${topics.n_positive_before} in the period before`}
      icon={<Compass aria-hidden="true" />}
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-5">
          <section className="flex flex-col gap-1.5">
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-relevant">
              <TrendingUp aria-hidden="true" className="size-4" />
              Leaning in
            </h3>
            {topics.leaning_in.length > 0 ? (
              <ul>
                {topics.leaning_in.map((stat) => (
                  <TrendRow key={stat.key} stat={stat} rising />
                ))}
              </ul>
            ) : (
              <p className="px-2 text-[13px] text-muted">No topic is clearly rising yet.</p>
            )}
          </section>
          {topics.fading.length > 0 ? (
            <section className="flex flex-col gap-1.5">
              <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-not-relevant">
                <TrendingDown aria-hidden="true" className="size-4" />
                Fading
              </h3>
              <ul>
                {topics.fading.map((stat) => (
                  <TrendRow key={stat.key} stat={stat} rising={false} />
                ))}
              </ul>
            </section>
          ) : null}
        </div>
        <div className="flex flex-col gap-5">
          <section className="flex flex-col gap-3">
            <h3 className="text-[13px] font-semibold text-text">Top topics {span}</h3>
            {topics.top.length > 0 ? <TopBars stats={topics.top} /> : <p className="text-[13px] text-muted">Nothing kept in this period.</p>}
          </section>
          {topics.skipping.length > 0 ? (
            <section className="flex flex-col gap-2">
              <h3 className="text-[13px] font-semibold text-text">Often skipped</h3>
              <ul className="flex flex-wrap gap-1.5">
                {topics.skipping.map((stat) => (
                  <li key={stat.key} className="rounded-pill border border-not-relevant/25 bg-not-relevant/8 px-2.5 py-1 text-[12.5px] text-text">
                    {stat.label} <span className="text-muted tabular-nums">· {percent(stat.skip_rate)} skipped</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      </div>
      {topics.coverage !== null && topics.coverage < 0.5 ? (
        <p className="text-[12px] text-faint">
          {percent(1 - topics.coverage)} of these papers are grouped by their arXiv category; OpenAlex topics arrive a few days after publication.
        </p>
      ) : null}
    </Panel>
  );
}
