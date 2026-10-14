/**
 * Landing navigation: the logo lockup, then HOW IT WORKS · GRAPH · DASHBOARD (in-page anchors to the
 * matching sections) and SIGN IN. Narrow screens keep Sign in (and How it works from 640 px).
 */
import { Link } from "react-router";
import { paths } from "../../app/paths";
import { useTheme } from "../../lib/theme";
import { LANDING_ASSETS, SECTION_IDS } from "./content";

const linkClass =
  "inline-flex min-h-11 items-center rounded-[6px] px-2.5 text-[13px] font-bold tracking-[0.02em] uppercase transition-colors hover:text-(--lp-link) sm:px-3 sm:text-[14px]";

export function LandingNav() {
  const { resolved } = useTheme();
  return (
    <header className="border-b border-(--lp-hairline)">
      <nav aria-label="Main" className="mx-auto flex h-[72px] w-full max-w-[1440px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-10">
        <Link to={paths.root()} className="-ml-1 inline-flex min-h-11 items-center rounded-[6px] px-1">
          <img
            src={resolved === "dark" ? LANDING_ASSETS.logoWhite : LANDING_ASSETS.logo}
            alt="Ratiocination home"
            width={LANDING_ASSETS.logoSize.width}
            height={LANDING_ASSETS.logoSize.height}
            className="h-[22px] w-auto"
          />
        </Link>
        <ul className="-mr-2.5 flex items-center sm:-mr-3">
          <li className="hidden sm:block">
            <a href={`#${SECTION_IDS.howItWorks}`} className={linkClass}>
              How it works
            </a>
          </li>
          <li className="hidden md:block">
            <a href={`#${SECTION_IDS.graph}`} className={linkClass}>
              Graph
            </a>
          </li>
          <li className="hidden md:block">
            <a href={`#${SECTION_IDS.why}`} className={linkClass}>
              Dashboard
            </a>
          </li>
          <li>
            <Link to={paths.signIn()} className={linkClass}>
              Sign in
            </Link>
          </li>
        </ul>
      </nav>
    </header>
  );
}
