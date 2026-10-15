/**
 * Landing navigation: the logo lockup, then HOW IT WORKS · GRAPH · DASHBOARD (in-page anchors to the
 * matching sections) and, last, either SIGN IN or — when `me` says the visitor is already signed in —
 * their avatar and "Open app" linking to `/` (which routes them to their project). The landing page is
 * reachable from inside the app (the shell's logo points at `/welcome`), so it must not tell a
 * signed-in user to sign in. `me` is already in the query cache for anyone who came from the app.
 * Narrow screens keep that last item (and How it works from 640 px).
 */
import { Link } from "react-router";
import { useMe } from "../../api/hooks";
import { paths } from "../../app/paths";
import { UserAvatar } from "../../app/shell/UserMenu";
import { useTheme } from "../../lib/theme";
import { LANDING_ASSETS, SECTION_IDS } from "./content";

const linkClass =
  "inline-flex min-h-11 items-center rounded-[6px] px-2.5 text-[13px] font-bold tracking-[0.02em] uppercase transition-colors hover:text-(--lp-link) sm:px-3 sm:text-[14px]";

export function LandingNav() {
  const { resolved } = useTheme();
  // 401 here is the normal signed-out answer, not an error worth retrying or reporting.
  const me = useMe({ retry: false });
  const user = me.data?.user ?? null;
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
            {user ? (
              <Link to={paths.root()} className={`${linkClass} gap-2`} aria-label={`Open the app as ${user.display_name || user.email || "your account"}`}>
                <UserAvatar user={user} size={26} />
                <span className="hidden sm:inline">Open app</span>
              </Link>
            ) : (
              <Link to={paths.signIn()} className={linkClass}>
                Sign in
              </Link>
            )}
          </li>
        </ul>
      </nav>
    </header>
  );
}
