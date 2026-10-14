/**
 * Disruption (UI.md §8.5): the CD index of a paper (Funk & Owen-Smith 2017; Park, Leahey & Funk
 * 2023) from its OpenAlex citations. Among the papers citing it, those that cite it *without* its
 * references (n_i) push towards disruptive; those citing it *together with* its references (n_j)
 * push towards consolidating; papers that cite only its references (n_k) dilute the index.
 */
import { RefreshCw, Zap } from "lucide-react";
import { toast } from "sonner";
import { errorMessage, isApiError } from "../../api/client";
import { useComputeDisruption } from "../../api/hooks";
import type { DisruptionDetail } from "../../api/types";
import { StackedBar, StackedLegend } from "../../components/charts/StackedBar";
import { Button } from "../../components/ui/Button";
import { formatDate, formatNumber } from "../../lib/format";
import { DISRUPTION_COLOR, DISRUPTION_TEXT, formatCd } from "./meta";

const STALE_MS = 30 * 24 * 3600 * 1000;

function computeErrorText(error: unknown): string {
  if (isApiError(error) && error.status === 422) return "This paper is not in OpenAlex yet, so its citations cannot be analysed.";
  if (isApiError(error) && error.code === "budget_exceeded") return "Today's OpenAlex budget is used up. Try again tomorrow.";
  return errorMessage(error, "Could not compute the disruption index.");
}

export function DisruptionCard({ paperId, detail }: { paperId: number; detail: DisruptionDetail | null }) {
  const compute = useComputeDisruption();
  const run = (force = false) =>
    compute.mutate({ paperId, force }, { onError: (error) => toast.error(computeErrorText(error)) });

  if (!detail) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-[13.5px] leading-relaxed text-muted">
          Does later work cite this paper <em>instead of</em> the papers it builds on (disruptive), or <em>together with</em> them
          (consolidating)? The CD index answers it from the citation network.
        </p>
        {compute.isError ? (
          <p role="alert" className="text-[13px] text-not-relevant">
            {computeErrorText(compute.error)}
          </p>
        ) : null}
        <div>
          <Button size="sm" leadingIcon={<Zap aria-hidden="true" />} onClick={() => run()} loading={compute.isPending}>
            Compute disruption
          </Button>
        </div>
      </div>
    );
  }

  const color = DISRUPTION_COLOR[detail.label];
  const citers = detail.n_i + detail.n_j;
  const parts = [
    { key: "i", label: "Cite it without its references", value: detail.n_i, color: "var(--disruptive)" },
    { key: "j", label: "Cite it with its references", value: detail.n_j, color: "var(--consolidating)" },
  ];
  const stale = Date.now() - new Date(detail.computed_at).getTime() > STALE_MS;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-[30px] leading-none font-semibold tabular-nums" style={{ color }}>
          {formatCd(detail.cd)}
        </span>
        <span
          className="inline-flex h-7 items-center gap-1.5 rounded-pill border px-2.5 text-[12.5px] font-medium"
          style={{ color, borderColor: `color-mix(in oklab, ${color} 40%, transparent)`, backgroundColor: `color-mix(in oklab, ${color} 10%, transparent)` }}
        >
          <Zap aria-hidden="true" className="size-3.5" />
          {DISRUPTION_TEXT[detail.label]}
        </span>
      </div>
      {citers > 0 ? (
        <div className="flex flex-col gap-2">
          <StackedBar parts={parts} label="Papers citing it" height={10} />
          <StackedLegend parts={parts} format={(value) => formatNumber(value)} />
        </div>
      ) : null}
      <p className="text-[13.5px] leading-relaxed text-text">{detail.text}</p>
      <p className="text-[12px] text-faint">
        CD ranges from −1 (consolidating) to +1 (disruptive); most papers sit near 0. {formatNumber(detail.n_k)} papers cite only
        its references. Based on {detail.refs_used}
        {detail.refs_total && detail.refs_total > detail.refs_used ? ` of ${detail.refs_total}` : ""} references · computed{" "}
        {formatDate(detail.computed_at)}.
      </p>
      {stale ? (
        <div>
          <Button size="sm" variant="ghost" leadingIcon={<RefreshCw aria-hidden="true" />} onClick={() => run(true)} loading={compute.isPending}>
            Recompute
          </Button>
        </div>
      ) : null}
    </div>
  );
}
