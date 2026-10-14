/**
 * The authors' breakthrough papers (UI.md §8.6): for each author of the paper (first six and the
 * last), their most cited works ranked by disruption (CD). Runs as a background job that queries
 * OpenAlex; a work is a "breakthrough" when it is clearly disruptive among enough citing papers.
 */
import { Crown, RefreshCw, Sparkles, Telescope } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "../../api/client";
import { useAnalyzeBreakthroughs, useBreakthroughs } from "../../api/hooks";
import type { ScholarBreakthroughs } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { RadarSweep } from "../../components/ui/RadarSweep";
import { Skeleton } from "../../components/ui/Skeleton";
import { formatDate, formatNumber } from "../../lib/format";
import { DISRUPTION_COLOR, cdLabelOf, formatCd } from "./meta";

const SHOWN = 4;

function ScholarRow({ entry, onOpenPaper }: { entry: ScholarBreakthroughs; onOpenPaper: (paperId: number) => void }) {
  const [all, setAll] = useState(false);
  const { scholar } = entry;
  const works = all ? entry.works : entry.works.slice(0, SHOWN);
  return (
    <li className="flex flex-col gap-2.5 rounded-panel border border-border bg-surface-2/40 p-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-[14px] font-semibold text-text">{scholar.display_name}</p>
        <p className="text-[12px] text-muted">
          {[
            scholar.institution,
            scholar.h_index !== null ? `h-index ${scholar.h_index}` : null,
            scholar.works_count !== null ? `${formatNumber(scholar.works_count)} works` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      {entry.works.length === 0 ? (
        <p className="text-[12.5px] text-faint">No analysed works.</p>
      ) : (
        <ol className="flex flex-col gap-1.5">
          {works.map((work) => {
            const label = cdLabelOf(work.cd);
            return (
              <li key={work.paper.id} className="flex items-start gap-2.5">
                <span className="mt-0.5 w-14 shrink-0 text-right font-mono text-[12.5px] tabular-nums" style={{ color: DISRUPTION_COLOR[label] }}>
                  {formatCd(work.cd)}
                </span>
                <span className="min-w-0 flex-1">
                  <button type="button" onClick={() => onOpenPaper(work.paper.id)} className="text-left font-serif text-[14px] leading-snug text-text hover:text-accent hover:underline">
                    {work.paper.title}
                  </button>
                  <span className="block text-[11.5px] text-faint">
                    {[work.paper.year, work.n_citing !== null ? `${formatNumber(work.n_citing)} citing papers` : null].filter(Boolean).join(" · ")}
                  </span>
                </span>
                {work.is_breakthrough ? (
                  <Badge tone="disruptive" size="sm" icon={<Crown aria-hidden="true" />}>
                    Breakthrough
                  </Badge>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      {entry.works.length > SHOWN ? (
        <button type="button" onClick={() => setAll((v) => !v)} className="self-start text-[12.5px] font-medium text-accent hover:underline">
          {all ? "Show fewer" : `Show all ${entry.works.length}`}
        </button>
      ) : null}
    </li>
  );
}

export function Breakthroughs({ paperId, onOpenPaper }: { paperId: number; onOpenPaper: (paperId: number) => void }) {
  const data = useBreakthroughs(paperId);
  const analyze = useAnalyzeBreakthroughs();
  const start = (force = false) =>
    analyze.mutate({ paperId, force }, { onError: (error) => toast.error(errorMessage(error, "Could not start the analysis")) });

  if (data.isPending) return <Skeleton className="h-20" />;
  if (data.isError) return <p className="text-[13px] text-not-relevant">{data.error.message}</p>;
  const result = data.data;

  if (result.status === "queued" || result.status === "running") {
    return (
      <RadarSweep
        size="sm"
        title="Reading the authors' citation records"
        message={result.job?.message ?? "Looking up the authors in OpenAlex…"}
        progress={result.job?.progress}
        label="Breakthrough analysis progress"
        className="py-4"
      />
    );
  }

  if (result.status === "none" || (result.status === "failed" && result.scholars.length === 0)) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-[13.5px] leading-relaxed text-muted">
          Which of these authors' papers changed their field? Ratiocination ranks each author's most cited works by how
          disruptive they were — whether later work cited them instead of what came before.
        </p>
        {result.status === "failed" ? (
          <p role="alert" className="text-[13px] text-not-relevant">
            {result.job?.error_message ?? result.message ?? "The last analysis failed."}
          </p>
        ) : null}
        <div>
          <Button size="sm" leadingIcon={<Telescope aria-hidden="true" />} onClick={() => start()} loading={analyze.isPending}>
            {result.status === "failed" ? "Try again" : "Find their breakthrough papers"}
          </Button>
        </div>
      </div>
    );
  }

  const found = result.scholars.reduce((n, entry) => n + entry.works.filter((work) => work.is_breakthrough).length, 0);
  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-center gap-2 text-[13px] text-muted">
        <Sparkles aria-hidden="true" className="size-4 text-disruptive" />
        {found > 0 ? `${found} breakthrough paper${found === 1 ? "" : "s"} among ${result.scholars.length} authors.` : `No clear breakthrough among ${result.scholars.length} authors' top works.`}
      </p>
      {result.message ? <p className="rounded-control border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-muted">{result.message}</p> : null}
      <ul className="flex flex-col gap-2.5">
        {result.scholars.map((entry) => (
          <ScholarRow key={entry.scholar.id} entry={entry} onOpenPaper={onOpenPaper} />
        ))}
      </ul>
      <div className="flex items-center justify-between gap-3 text-[12px] text-faint">
        <span>{result.analyzed_at ? `Analysed ${formatDate(result.analyzed_at)} from OpenAlex` : null}</span>
        <Button size="sm" variant="ghost" leadingIcon={<RefreshCw aria-hidden="true" />} onClick={() => start(true)} loading={analyze.isPending}>
          Re-run
        </Button>
      </div>
    </div>
  );
}
