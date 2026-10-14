/** "Why this score" (UI.md §8.3): stacked feature contributions and the raw numbers behind them. */
import type { FeatureName, FeatureScore, RecommendationItem } from "../../api/types";
import { StackedBar } from "../../components/charts/StackedBar";
import { formatScore } from "../../lib/format";

export const FEATURE_LABELS: Readonly<Record<FeatureName, string>> = {
  question: "Research question",
  anchor: "Similar to your papers",
  keyword: "Your keywords",
  draft: "Your draft",
  citation: "Citation link",
  preference: "Your ratings",
  recency: "Recency",
};

export const FEATURE_ORDER: readonly FeatureName[] = ["question", "anchor", "keyword", "draft", "citation", "preference", "recency"];

/** A feature the item does not report (a deck built before that feature existed). */
const MISSING: FeatureScore = { value: null, raw: null, weight: 0, contribution: 0, available: false };

function featureOf(item: RecommendationItem, name: FeatureName): FeatureScore {
  return item.features[name] ?? MISSING;
}

/**
 * The bar spans the maximum possible score (the sum of the weights): available features fill their
 * contribution, unavailable ones show their whole weight hatched in grey (score the paper could not
 * earn), and the empty track is what it could have earned but did not.
 */
export function ScoreBreakdown({ item }: { item: RecommendationItem }) {
  const maxScore = FEATURE_ORDER.reduce((sum, name) => sum + featureOf(item, name).weight, 0);
  const parts = FEATURE_ORDER.map((name) => {
    const feature = featureOf(item, name);
    return {
      key: name,
      label: feature.available ? FEATURE_LABELS[name] : `${FEATURE_LABELS[name]} (not available)`,
      value: feature.available ? Math.max(0, feature.contribution) : feature.weight,
      color: feature.available ? `var(--feat-${name})` : "var(--text-faint)",
      hatched: !feature.available,
    };
  });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] text-muted">Score</span>
        <span className="font-mono text-[15px] font-semibold text-text">
          {formatScore(item.score, 3)}
          <span className="font-normal text-faint"> / {formatScore(maxScore, 2)}</span>
        </span>
      </div>
      <StackedBar parts={parts} total={maxScore} label="Score contributions" height={14} />
      <table className="w-full text-[12.5px]">
        <caption className="sr-only">Feature scores</caption>
        <thead>
          <tr className="text-left text-faint">
            <th scope="col" className="py-1 font-medium">Feature</th>
            <th scope="col" className="py-1 text-right font-medium">Raw</th>
            <th scope="col" className="py-1 text-right font-medium">Value</th>
            <th scope="col" className="py-1 text-right font-medium">Weight</th>
            <th scope="col" className="py-1 text-right font-medium">Contribution</th>
          </tr>
        </thead>
        <tbody>
          {FEATURE_ORDER.map((name) => {
            const feature = featureOf(item, name);
            return (
              <tr key={name} className="border-t border-border">
                <th scope="row" className="py-1.5 text-left font-normal text-text">
                  <span className="mr-2 inline-block size-2 rounded-full align-middle" style={{ backgroundColor: `var(--feat-${name})` }} aria-hidden="true" />
                  {FEATURE_LABELS[name]}
                </th>
                {feature.available ? (
                  <>
                    <td className="py-1.5 text-right font-mono text-muted">{formatScore(feature.raw, 3)}</td>
                    <td className="py-1.5 text-right font-mono text-muted">{formatScore(feature.value, 2)}</td>
                    <td className="py-1.5 text-right font-mono text-muted">{formatScore(feature.weight, 2)}</td>
                    <td className="py-1.5 text-right font-mono text-text">{formatScore(feature.contribution, 3)}</td>
                  </>
                ) : (
                  <td colSpan={4} className="py-1.5 text-right text-faint italic">
                    not available
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
