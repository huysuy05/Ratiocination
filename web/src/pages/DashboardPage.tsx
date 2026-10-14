/**
 * The per-user dashboard (UI.md §12): how much you read and swiped, the topics you lean into, what
 * the papers you kept found, and how they relate to your work. Period (`?days=`) and project
 * (`?project=`) live in the URL; the time zone is your preference, else the browser's.
 */
import { CircleAlert } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import {
  browserTimeZone,
  parseId,
  useDashboardActivity,
  useDashboardFindings,
  useDashboardTopics,
  useDashboardWork,
  useMe,
  useProjects,
  type DashboardParams,
} from "../api/hooks";
import type { DashboardDays } from "../api/types";
import { usePaperSheetParam } from "../app/params";
import { Button } from "../components/ui/Button";
import { PageHeader } from "../components/ui/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { ActivitySection } from "../features/dashboard/ActivitySection";
import { FindingsSection } from "../features/dashboard/FindingsSection";
import { DAY_OPTIONS, parseDays } from "../features/dashboard/format";
import { PulseSection } from "../features/dashboard/PulseSection";
import { TopicsSection } from "../features/dashboard/TopicsSection";
import { WorkSection } from "../features/dashboard/WorkSection";
import { PaperDetailSheet } from "../features/paper/PaperDetailSheet";

const DAY_LABELS: Readonly<Record<DashboardDays, string>> = { 7: "7 days", 30: "30 days", 90: "90 days", 365: "Year" };

function SectionError({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-card border border-not-relevant/30 bg-not-relevant/6 px-5 py-4">
      <CircleAlert aria-hidden="true" className="size-4 text-not-relevant" />
      <p className="min-w-0 flex-1 text-[13.5px] text-text">
        {title}: {message}
      </p>
      <Button size="sm" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

export default function DashboardPage() {
  const [search, setSearch] = useSearchParams();
  const days = parseDays(search.get("days"));
  const projectId = parseId(search.get("project"));
  const me = useMe();
  const projects = useProjects();
  const sheet = usePaperSheetParam();
  const tz = me.data?.user.preferences.timezone ?? browserTimeZone();
  const params: DashboardParams = useMemo(() => ({ days, projectId, tz }), [days, projectId, tz]);
  // Wait for the user's time-zone preference so periods are never fetched twice (or in the wrong zone).
  const ready = { enabled: me.data !== undefined || me.isError };
  const activity = useDashboardActivity(params, ready);
  const topics = useDashboardTopics(params, ready);
  const findings = useDashboardFindings(params, ready);
  const work = useDashboardWork(params, ready);

  const update = (next: { days?: DashboardDays; project?: number | null }) =>
    setSearch(
      (previous) => {
        const out = new URLSearchParams(previous);
        if (next.days !== undefined) {
          if (next.days === 30) out.delete("days");
          else out.set("days", String(next.days));
        }
        if (next.project !== undefined) {
          if (next.project === null) out.delete("project");
          else out.set("project", String(next.project));
        }
        out.delete("paper");
        return out;
      },
      { replace: true },
    );

  const section = (query: { isError: boolean; error: { message: string } | null; refetch: () => unknown }, title: string, node: ReactNode) =>
    query.isError ? <SectionError title={title} message={query.error?.message ?? "failed"} onRetry={() => void query.refetch()} /> : node;

  return (
    <section aria-labelledby="page-title" className="mx-auto flex w-full max-w-7xl flex-col gap-5">
      <PageHeader
        title="Dashboard"
        description="How your reading is going, the topics you lean into, what the papers found and how they connect to your work."
        className="!mb-1"
      />
      <div className="flex flex-wrap items-center gap-3">
        <SegmentedControl
          label="Period"
          value={String(days)}
          onValueChange={(value) => update({ days: Number(value) as DashboardDays })}
          options={DAY_OPTIONS.map((value) => ({ value: String(value), label: DAY_LABELS[value] }))}
        />
        <label className="flex items-center gap-2 text-[13px] text-muted">
          <span>Project</span>
          <select
            value={projectId ?? ""}
            onChange={(event) => update({ project: event.target.value ? Number(event.target.value) : null })}
            className="h-9 rounded-control border border-border bg-surface-2 px-3 text-[13.5px] text-text hover:border-border-strong focus:border-accent"
          >
            <option value="">All projects</option>
            {(projects.data ?? []).map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        <span className="ml-auto text-[12px] text-faint">Times in {tz}</span>
      </div>
      {section(
        activity,
        "Activity",
        <>
          <PulseSection activity={activity.data} loading={activity.isPending} />
          <ActivitySection activity={activity.data} loading={activity.isPending} />
        </>,
      )}
      {section(topics, "Topics", <TopicsSection topics={topics.data} loading={topics.isPending} />)}
      {section(findings, "Findings", <FindingsSection findings={findings.data} loading={findings.isPending} params={params} onOpenPaper={sheet.openPaper} />)}
      {section(work, "Relation to your work", <WorkSection work={work.data} loading={work.isPending} onOpenPaper={sheet.openPaper} />)}
      <PaperDetailSheet paperId={sheet.paperId} projectId={projectId} source="dashboard" onClose={sheet.closePaper} />
    </section>
  );
}
