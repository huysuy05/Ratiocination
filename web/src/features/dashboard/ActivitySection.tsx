/** Activity (UI.md §12.2): swipes per day/week with the positives highlighted, and per project. */
import { BarChart3 } from "lucide-react";
import type { DashboardActivity } from "../../api/types";
import { ActivityBars } from "../../components/charts/ActivityBars";
import { Skeleton } from "../../components/ui/Skeleton";
import { Panel } from "./Panel";
import { bucketLabel, periodPhrase } from "./format";

export function ActivitySection({ activity, loading }: { activity: DashboardActivity | undefined; loading: boolean }) {
  if (loading || !activity) return <Skeleton className="h-[320px] rounded-card" />;
  const data = activity.series.map((point) => ({
    key: point.date,
    label: bucketLabel(point.date, activity.period),
    total: point.swiped,
    highlight: point.positive,
  }));
  const maxProject = Math.max(1, ...activity.by_project.map((project) => project.swiped));
  const empty = activity.totals.swiped === 0;
  return (
    <Panel
      title="Activity"
      subtitle={`Papers you swiped per ${activity.period.bucket}, ${periodPhrase(activity.period.days)}`}
      icon={<BarChart3 aria-hidden="true" />}
      actions={
        <div className="flex items-center gap-3 text-[12px] text-muted" aria-hidden="true">
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-[color-mix(in_oklab,var(--accent)_22%,var(--surface-2))]" />
            swiped
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-accent" />
            relevant or saved
          </span>
        </div>
      }
    >
      {empty ? (
        <p className="py-10 text-center text-[13.5px] text-muted">No swipes in this period yet. Your deck is waiting.</p>
      ) : (
        <div className="pl-6">
          <ActivityBars
            data={data}
            label={`Swiped and kept papers per ${activity.period.bucket}`}
            totalLabel="Swiped"
            highlightLabel="Kept"
          />
        </div>
      )}
      {activity.by_project.length > 1 ? (
        <ul className="flex flex-col gap-2 border-t border-border pt-4" aria-label="By project">
          {activity.by_project.map((project) => (
            <li key={project.project_id} className="flex items-center gap-3 text-[13px]">
              <span className="w-40 truncate text-text">{project.name}</span>
              <span className="relative h-2 flex-1 overflow-hidden rounded-pill bg-surface-3">
                <span className="absolute inset-y-0 left-0 rounded-pill bg-[color-mix(in_oklab,var(--accent)_30%,var(--surface-3))]" style={{ width: `${(project.swiped / maxProject) * 100}%` }} />
                <span className="absolute inset-y-0 left-0 rounded-pill bg-accent" style={{ width: `${(project.positive / maxProject) * 100}%` }} />
              </span>
              <span className="w-20 text-right text-muted tabular-nums">
                {project.positive}/{project.swiped}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
