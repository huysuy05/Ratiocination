/** "What it is" and "Get started": centred eyebrow, bold headline and pill buttons. */
import { Link } from "react-router";
import { paths } from "../../app/paths";
import { SECTION_IDS } from "./content";
import { PillLink } from "./PillLink";
import { Reveal } from "./Reveal";

const headlineClass =
  "mx-auto mt-4 max-w-[760px] text-[28px] leading-[1.18] font-bold tracking-[-0.015em] text-balance sm:text-[34px] lg:text-[40px] lg:leading-[44px]";

export function IntroSection() {
  return (
    <section aria-labelledby="intro-title" className="px-4 pt-20 pb-14 text-center sm:px-6 lg:pt-[132px] lg:pb-[80px]">
      <Reveal>
        <p className="lp-eyebrow text-(--lp-muted)">What it is</p>
        <h2 id="intro-title" className={headlineClass}>
          We’re Ratiocination, and we’re here to help you find the papers that matter.
        </h2>
        <PillLink href={`#${SECTION_IDS.howItWorks}`} className="mt-7">
          How it works
        </PillLink>
      </Reveal>
    </section>
  );
}

export function GetStartedSection() {
  return (
    <section aria-labelledby="get-started-title" className="px-4 pt-20 pb-20 text-center sm:px-6 lg:pt-[120px] lg:pb-[110px]">
      <Reveal>
        <p className="lp-eyebrow text-(--lp-muted)">Get started</p>
        <h2 id="get-started-title" className={headlineClass}>
          Sign in with Google, GitHub or email, and your first deck starts building right away.
        </h2>
        <div className="mt-7 flex flex-wrap items-center justify-center gap-x-6 gap-y-3">
          <PillLink to={paths.signUp()}>Create an account</PillLink>
          <Link
            to={paths.signIn()}
            className="inline-flex min-h-11 items-center rounded-[4px] px-2 text-[15px] font-semibold text-(--lp-link) underline-offset-4 hover:underline"
          >
            Sign in
          </Link>
        </div>
      </Reveal>
    </section>
  );
}
