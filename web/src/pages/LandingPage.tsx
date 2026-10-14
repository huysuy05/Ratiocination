/**
 * Public landing page (UI.md §16): `/` for signed-out visitors, `/welcome` for everyone. Lazy-loaded
 * with its own font (Outfit) and styles; no API calls.
 */
import "@fontsource-variable/outfit";
import "../features/landing/landing.css";

import { useLayoutEffect } from "react";
import { CategoryMarquee } from "../features/landing/CategoryMarquee";
import { HeroSection } from "../features/landing/HeroSection";
import { HowItWorks } from "../features/landing/HowItWorks";
import { GetStartedSection, IntroSection } from "../features/landing/IntroSection";
import { LandingFooter } from "../features/landing/LandingFooter";
import { LandingNav } from "../features/landing/LandingNav";
import { StatementCard } from "../features/landing/StatementCard";

export default function LandingPage() {
  // The page background (and overscroll) follows the landing tokens while mounted. Leaving starts
  // the next page (sign in, sign up, the app) at the top, not at the landing's scroll position.
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.classList.add("lp-page");
    return () => {
      root.classList.remove("lp-page");
      window.scrollTo(0, 0);
    };
  }, []);

  return (
    <div className="lp-root min-h-dvh overflow-x-clip">
      <a
        href="#main"
        className="sr-only z-50 rounded-[4px] bg-(--lp-button) px-4 py-2.5 text-[14px] font-bold text-(--lp-on-button) focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <LandingNav />
      <main id="main" tabIndex={-1}>
        <HeroSection />
        <IntroSection />
        <CategoryMarquee />
        <HowItWorks />
        <div className="pt-12 lg:pt-[88px]">
          <StatementCard />
        </div>
        <GetStartedSection />
      </main>
      <LandingFooter />
    </div>
  );
}
