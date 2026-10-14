import clsx from "clsx";
import { ChevronRight, Ellipsis, LogOut, type LucideIcon } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router";
import { BrandMark } from "../../components/ui/BrandMark";
import { Sheet } from "../../components/ui/Sheet";
import { SPRING } from "../../lib/motion";
import { useMe } from "../../api/hooks";
import { DASHBOARD_NAV, MOBILE_MORE_PROJECT_KEYS, MOBILE_TAB_KEYS, PROJECT_NAV, PROJECTS_NAV, SETTINGS_NAV } from "../nav";
import { paths } from "../paths";
import { PrivacyBadge } from "./PrivacyBadge";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { ThemeCycleButton, ThemeSegmented } from "./ThemeSwitcher";
import { UserMenu, useSignOutAndLeave } from "./UserMenu";

/** < 768 px top bar: logo, project switcher, theme, account (UI.md §4). */
export function TopBar({ projectId }: { projectId: number | null }) {
  return (
    <header className="glass sticky top-0 z-30 flex h-[calc(3.5rem+env(safe-area-inset-top))] shrink-0 items-center gap-1 border-b border-border px-2 pt-[env(safe-area-inset-top)] md:hidden">
      <Link to={paths.root()} aria-label="Ratiocination home" className="grid size-11 shrink-0 place-items-center rounded-button text-text">
        <BrandMark size={26} />
      </Link>
      <ProjectSwitcher projectId={projectId} variant="topbar" className="min-w-0 flex-1" />
      <ThemeCycleButton />
      <UserMenu variant="topbar" />
    </header>
  );
}

const TAB_CLASS =
  "group relative flex h-full w-full flex-col items-center justify-center gap-1 text-[11px] font-medium transition-colors duration-150";

function TabContent({ icon: Icon, label, active }: { icon: LucideIcon; label: string; active: boolean }) {
  return (
    <>
      <span className="relative grid h-7 w-14 place-items-center">
        {active ? (
          <motion.span
            layoutId="rr-tab-active"
            transition={SPRING}
            aria-hidden="true"
            className="absolute inset-0 rounded-pill bg-accent/14 shadow-[0_0_18px_-6px_var(--accent-glow)]"
          />
        ) : null}
        <Icon aria-hidden="true" className={clsx("relative size-5", active ? "text-accent" : "text-faint group-hover:text-muted")} />
      </span>
      <span className={clsx(active ? "text-text" : "text-muted")}>{label}</span>
    </>
  );
}

function MoreRow({ to, label, icon: Icon, onNavigate }: { to: string; label: string; icon: LucideIcon; onNavigate: () => void }) {
  return (
    <li>
      <NavLink
        to={to}
        onClick={onNavigate}
        className={({ isActive }) =>
          clsx(
            "flex min-h-12 items-center gap-3 px-4 text-[15px] font-medium transition-colors",
            isActive ? "bg-accent/10 text-text" : "text-text hover:bg-surface-2",
          )
        }
      >
        {({ isActive }) => (
          <>
            <Icon aria-hidden="true" className={clsx("size-5 shrink-0", isActive ? "text-accent" : "text-faint")} />
            <span className="min-w-0 flex-1 truncate">{label}</span>
            <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-faint" />
          </>
        )}
      </NavLink>
    </li>
  );
}

const MORE_GROUP_CLASS = "overflow-hidden rounded-panel border border-border bg-surface-2/60 divide-y divide-border";

const MORE_WORKSPACE = [PROJECTS_NAV, SETTINGS_NAV] as const;

/** "More" tab: a full-screen sheet with the remaining pages, theme, privacy and sign-out (UI.md §4). */
function MoreSheet({ projectId }: { projectId: number | null }) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const { data: me } = useMe();
  const { signOut, pending: signingOut } = useSignOutAndLeave();
  const close = () => setOpen(false);
  const moreItems = PROJECT_NAV.filter((item) => (MOBILE_MORE_PROJECT_KEYS as readonly string[]).includes(item.key));
  const moreActive =
    projectId !== null && moreItems.some((item) => location.pathname === item.to(projectId))
      ? true
      : MORE_WORKSPACE.some((item) => location.pathname === item.to);

  // Close when the route changes by other means (Back button, links inside pages).
  useEffect(() => {
    setOpen(false);
  }, [location.key]);

  return (
    <Sheet
      open={open}
      onOpenChange={setOpen}
      side="bottom"
      title="More"
      trigger={
        <button type="button" className={clsx(TAB_CLASS, "hover:text-text")} aria-haspopup="dialog">
          <TabContent icon={Ellipsis} label="More" active={moreActive} />
        </button>
      }
      bodyClassName="flex flex-col gap-6"
    >
      <nav aria-label="More" className="flex flex-col gap-6">
        {projectId !== null ? (
          <section className="flex flex-col gap-2">
            <h3 className="px-1 text-eyebrow">Project</h3>
            <ul className={MORE_GROUP_CLASS}>
              {moreItems.map((item) => (
                <MoreRow key={item.key} to={item.to(projectId)} label={item.label} icon={item.icon} onNavigate={close} />
              ))}
            </ul>
          </section>
        ) : null}
        <section className="flex flex-col gap-2">
          <h3 className="px-1 text-eyebrow">Workspace</h3>
          <ul className={MORE_GROUP_CLASS}>
            {MORE_WORKSPACE.map((item) => (
              <MoreRow key={item.key} to={item.to} label={item.label} icon={item.icon} onNavigate={close} />
            ))}
          </ul>
        </section>
      </nav>
      <section className="flex flex-col gap-2">
        <h3 className="px-1 text-eyebrow">Appearance</h3>
        <ThemeSegmented />
      </section>
      <PrivacyBadge />
      {me?.mode === "accounts" ? (
        <button
          type="button"
          onClick={signOut}
          disabled={signingOut}
          className="flex min-h-12 items-center gap-3 rounded-panel border border-border bg-surface-2/60 px-4 text-[15px] font-medium text-not-relevant transition-colors hover:bg-surface-2 disabled:opacity-50"
        >
          <LogOut aria-hidden="true" className="size-5" />
          Sign out{me.user.email ? <span className="ml-auto truncate text-[13px] font-normal text-muted">{me.user.email}</span> : null}
        </button>
      ) : null}
    </Sheet>
  );
}

/** < 768 px bottom tabs: Feed, Library, Graph, Dashboard, More (UI.md §4). */
export function BottomTabs({ projectId }: { projectId: number | null }) {
  const tabs = PROJECT_NAV.filter((item) => (MOBILE_TAB_KEYS as readonly string[]).includes(item.key));
  return (
    <nav
      aria-label="Tabs"
      className="glass fixed inset-x-0 bottom-0 z-30 border-t border-border pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <ul className="mx-auto flex h-16 max-w-xl items-stretch px-1">
        {tabs.map((item) => (
          <li key={item.key} className="min-w-0 flex-1">
            {projectId !== null ? (
              <NavLink to={item.to(projectId)} className={clsx(TAB_CLASS, "hover:text-text")}>
                {({ isActive }) => <TabContent icon={item.icon} label={item.label} active={isActive} />}
              </NavLink>
            ) : (
              <Link to={paths.projects()} className={clsx(TAB_CLASS, "hover:text-text")}>
                <TabContent icon={item.icon} label={item.label} active={false} />
              </Link>
            )}
          </li>
        ))}
        <li className="min-w-0 flex-1">
          <NavLink to={DASHBOARD_NAV.to} className={clsx(TAB_CLASS, "hover:text-text")}>
            {({ isActive }) => <TabContent icon={DASHBOARD_NAV.icon} label={DASHBOARD_NAV.label} active={isActive} />}
          </NavLink>
        </li>
        <li className="min-w-0 flex-1">
          <MoreSheet projectId={projectId} />
        </li>
      </ul>
    </nav>
  );
}
