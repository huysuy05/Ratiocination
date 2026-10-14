/**
 * "How it works": the header with a full-width rule, then the FEATURES label column and three
 * feature rows (title + paragraph, mock panel) separated by dotted rules. Rows stack below 1024 px;
 * the label gets its own column from 1360 px.
 */
import type { ComponentType } from "react";
import { FEATURES, SECTION_IDS, type Feature } from "./content";
import { GraphMock, SwipeMock, WhyMock } from "./FeatureMocks";
import { Reveal } from "./Reveal";

const ROWS: readonly { feature: Feature; Mock: ComponentType }[] = [
  { feature: FEATURES.deck, Mock: SwipeMock },
  { feature: FEATURES.why, Mock: WhyMock },
  { feature: FEATURES.graph, Mock: GraphMock },
];

function FeatureRow({ feature, Mock }: { feature: Feature; Mock: ComponentType }) {
  const titleId = `${feature.id}-title`;
  return (
    <article
      id={feature.id}
      aria-labelledby={titleId}
      className="grid scroll-mt-6 gap-8 border-t border-dotted border-(--lp-muted)/45 py-12 first:border-t-0 first:pt-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,520px)] lg:gap-10 lg:py-11 lg:first:pt-[100px] min-[1360px]:grid-cols-[minmax(0,1fr)_559px]"
    >
      <Reveal>
        <h3 id={titleId} className="text-[32px] leading-[1.1] font-bold tracking-[-0.01em] uppercase sm:text-[42px] lg:text-[44px] min-[1360px]:text-[50px] min-[1360px]:leading-[55px]">
          {feature.title[0]}
          <br />
          {feature.title[1]}
        </h3>
        <p className="mt-5 max-w-[410px] text-[16px] leading-[1.6] text-(--lp-muted)">{feature.body}</p>
      </Reveal>
      <div className="flex justify-center lg:block">
        <Mock />
      </div>
    </article>
  );
}

export function HowItWorks() {
  return (
    <section id={SECTION_IDS.howItWorks} aria-labelledby="how-title" className="mx-auto w-full max-w-[1440px] scroll-mt-6 px-4 pt-20 sm:px-6 lg:px-10 lg:pt-[140px]">
      <Reveal>
        <h2 id="how-title" className="text-center text-[34px] leading-none font-bold tracking-[-0.01em] uppercase sm:text-[44px] lg:text-[50px]">
          How it works
        </h2>
        <p className="mx-auto mt-4 max-w-[560px] text-center text-[15px] text-(--lp-muted) sm:text-[16px]">
          A short daily deck, a reason for every card, and a map of where it all connects.
        </p>
      </Reveal>
      <hr className="mt-7 border-0 border-t border-(--lp-text)" />
      <div className="min-[1360px]:grid min-[1360px]:grid-cols-[237px_minmax(0,1fr)]">
        <p className="pt-10 text-[12px] font-bold tracking-[0.08em] text-(--lp-muted) uppercase min-[1360px]:pt-[108px]">Features</p>
        <div>
          {ROWS.map(({ feature, Mock }) => (
            <FeatureRow key={feature.id} feature={feature} Mock={Mock} />
          ))}
        </div>
      </div>
    </section>
  );
}

