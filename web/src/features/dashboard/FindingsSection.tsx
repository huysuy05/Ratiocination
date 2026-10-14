/**
 * Main findings (UI.md §12.4): the optional AI summary — clearly labelled as written by Claude from
 * the public abstracts, every point citing its papers — and the verbatim quotes: each liked paper's
 * most finding-like abstract sentence, grouped by topic, with the result cues highlighted.
 */
import clsx from "clsx";
import { LockKeyhole, Quote, RefreshCw, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { isApiError } from "../../api/client";
import { isJobActive, useRequestSummary, type DashboardParams } from "../../api/hooks";
import type { DashboardFindings, Finding } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { RadarSweep } from "../../components/ui/RadarSweep";
import { Skeleton } from "../../components/ui/Skeleton";
import { formatDateTime } from "../../lib/format";
import { STATE_META } from "../../lib/states";
import { percent, periodPhrase } from "./format";
import { Panel } from "./Panel";

/** Result cues and numbers, mirroring the backend's finding score (SPEC §14.3). */
const CUES =
  /\b(?:we (?:show|find|found|demonstrate|prove|observe|report|establish)|(?:results?|experiments?|evaluations?|analys[ie]s|findings) (?:show|indicate|suggest|demonstrate|reveal|confirm)s?|outperform\w*|surpass\w*|improv\w*|reduc\w*|achiev\w*|state[- ]of[- ]the[- ]art|significant\w*|reveal\w*)\b|\d+(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?x\b/gi;

export function highlightCues(sentence: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of sentence.matchAll(CUES)) {
    const start = match.index ?? 0;
    if (start > last) out.push(sentence.slice(last, start));
    out.push(
      <mark key={start} className="rounded-[4px] bg-accent/14 px-0.5 text-text not-italic">
        {match[0]}
      </mark>,
    );
    last = start + match[0].length;
  }
  if (last < sentence.length) out.push(sentence.slice(last));
  return out;
}

function summaryErrorText(error: unknown): string {
  if (!isApiError(error)) return "Could not start the summary.";
  if (error.code === "quota_exceeded") return "You have used today's AI summaries. Try again tomorrow.";
  return error.message;
}

function AiSummaryCard({ findings, params, onOpenPaper }: { findings: DashboardFindings; params: DashboardParams; onOpenPaper: (id: number) => void }) {
  const request = useRequestSummary();
  const { ai, summary } = findings;
  if (!ai.available) {
    return (
      <p className="flex items-start gap-2 rounded-panel border border-border bg-surface-2/50 px-4 py-3 text-[13px] text-muted">
        <Sparkles aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-faint" />
        AI summaries are off on this server. The quotes below are taken word for word from the abstracts.
      </p>
    );
  }
  const running = isJobActive(ai.job) || request.isPending;
  const failed = ai.job?.status === "failed" ? ai.job : null;
  const canRun = ai.remaining_today > 0 && findings.n_papers >= 3;
  const start = () =>
    request.mutate(
      { days: params.days, project_id: params.projectId, tz: params.tz },
      { onError: (error) => toast.error(summaryErrorText(error)) },
    );

  return (
    <div className="relative overflow-hidden rounded-panel border border-accent/25 bg-[linear-gradient(135deg,color-mix(in_oklab,var(--accent)_8%,var(--surface)),var(--surface))] p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles aria-hidden="true" className="size-4 text-accent" />
        <h3 className="text-[15px] font-semibold text-text">AI summary</h3>
        <Badge tone="accent" size="sm">
          Written by Claude
        </Badge>
        {summary?.stale ? (
          <Badge tone="save" size="sm" variant="dashed">
            Stale — your liked papers changed
          </Badge>
        ) : null}
      </div>
      {running ? (
        <RadarSweep size="sm" className="py-6" title="Reading the abstracts" message={ai.job?.message ?? "Asking Claude…"} progress={ai.job?.progress} label="Summary progress" />
      ) : summary ? (
        <div className="mt-4 flex flex-col gap-5">
          {summary.themes.map((theme) => (
            <section key={theme.title} className="flex flex-col gap-2">
              <h4 className="text-[14px] font-semibold text-text">{theme.title}</h4>
              <ul className="flex flex-col gap-2.5">
                {theme.points.map((point, index) => (
                  <li key={index} className="flex flex-col gap-1.5 border-l-2 border-accent/35 pl-3">
                    <p className="text-[14px] leading-relaxed text-text">{point.text}</p>
                    <div className="flex flex-wrap gap-1.5">
                      {point.papers.map((paper) => (
                        <button
                          key={paper.id}
                          type="button"
                          onClick={() => onOpenPaper(paper.id)}
                          className="max-w-full truncate rounded-pill border border-border bg-surface px-2 py-0.5 text-left text-[11.5px] text-muted transition-colors hover:border-accent/40 hover:text-text"
                          title={paper.title}
                        >
                          {paper.title}
                        </button>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 text-[12px] text-faint">
            <span>
              AI-written from {summary.n_papers} abstracts · {summary.model} · {formatDateTime(summary.created_at)}
              {summary.dropped_points > 0 ? ` · ${summary.dropped_points} uncited point${summary.dropped_points === 1 ? "" : "s"} removed` : ""}
            </span>
            <Button size="sm" variant="ghost" leadingIcon={<RefreshCw aria-hidden="true" />} onClick={start} disabled={!canRun}>
              Refresh ({ai.remaining_today} left today)
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          <p className="text-[13.5px] leading-relaxed text-muted">
            Claude reads the abstracts of the papers you kept and writes what they found, theme by theme. Every point cites the papers it
            comes from.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" leadingIcon={<Sparkles aria-hidden="true" />} onClick={start} disabled={!canRun}>
              Summarize with Claude
            </Button>
            <span className="text-[12px] text-faint">
              {findings.n_papers < 3 ? "Needs at least 3 kept papers in this period" : `${ai.remaining_today} left today`}
            </span>
          </div>
        </div>
      )}
      {failed && !running ? (
        <p role="alert" className="mt-3 text-[13px] text-not-relevant">
          The last summary failed: {failed.error_message ?? "unknown error"}
        </p>
      ) : null}
      <p className="mt-3 flex items-center gap-1.5 text-[11.5px] text-faint">
        <LockKeyhole aria-hidden="true" className="size-3" />
        Only public titles and abstracts are sent to Anthropic — never your question, notes or documents.
      </p>
    </div>
  );
}

function QuoteCard({ finding, onOpenPaper }: { finding: Finding; onOpenPaper: (id: number) => void }) {
  const meta = STATE_META[finding.action === "save" ? "saved" : "relevant"];
  return (
    <figure className="flex flex-col gap-2.5 rounded-panel border border-border bg-surface-2/40 p-4">
      <blockquote className="font-serif text-[15px] leading-relaxed text-text italic">“{highlightCues(finding.sentence)}”</blockquote>
      <figcaption className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted">
        <button type="button" onClick={() => onOpenPaper(finding.paper.id)} className="max-w-full truncate text-left font-medium text-text hover:text-accent hover:underline">
          {finding.paper.title}
        </button>
        <span style={{ color: meta.color }}>· {meta.label.toLowerCase()}</span>
        {finding.similarity !== null ? <span>· {percent(Math.max(0, finding.similarity))} close to your work</span> : null}
      </figcaption>
    </figure>
  );
}

export function FindingsSection({
  findings,
  loading,
  params,
  onOpenPaper,
}: {
  findings: DashboardFindings | undefined;
  loading: boolean;
  params: DashboardParams;
  onOpenPaper: (id: number) => void;
}) {
  if (loading || !findings) return <Skeleton className="h-[420px] rounded-card" />;
  return (
    <Panel
      title="Main findings"
      subtitle={`What the ${findings.n_papers} papers you kept ${periodPhrase(findings.period.days)} found`}
      icon={<Quote aria-hidden="true" />}
    >
      <AiSummaryCard findings={findings} params={params} onOpenPaper={onOpenPaper} />
      {findings.groups.length === 0 ? (
        <p className="py-4 text-center text-[13.5px] text-muted">
          {findings.n_papers === 0 ? "Keep a few papers and their key findings appear here." : "None of these abstracts states a clear result yet."}
        </p>
      ) : (
        <div className="flex flex-col gap-6">
          <p className="text-[12.5px] text-faint">
            Quoted word for word: the most result-like sentence of {findings.n_with_findings} of {findings.n_papers} abstracts.
          </p>
          {findings.groups.map((group) => (
            <section key={group.topic.key} className="flex flex-col gap-2.5">
              <h3 className={clsx("text-[13px] font-semibold text-text")}>{group.topic.label}</h3>
              <div className="grid gap-3 md:grid-cols-2">
                {group.findings.map((finding) => (
                  <QuoteCard key={finding.paper.id} finding={finding} onOpenPaper={onOpenPaper} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </Panel>
  );
}
