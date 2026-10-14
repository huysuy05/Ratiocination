import clsx from "clsx";
import type { LucideIcon } from "lucide-react";
import { motion } from "motion/react";
import { Link, NavLink } from "react-router";
import { BrandLockup, BrandMark } from "../../components/ui/BrandMark";
import { Tooltip } from "../../components/ui/Tooltip";
import { SPRING } from "../../lib/motion";
import { GLOBAL_NAV, PROJECT_NAV } from "../nav";
import { paths } from "../paths";
import { PrivacyBadge } from "./PrivacyBadge";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { ThemeCycleButton, ThemeSegmented } from "./ThemeSwitcher";
import { UserMenu } from "./UserMenu";
import { useMediaQuery, WIDE_SIDEBAR_QUERY } from "./useMediaQuery";

interface SidebarLinkProps {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  /** Icon rail (768–1023 px): labels become tooltips. */
  rail: boolean;
}

function SidebarLink({ to, label, icon: Icon, end, rail }: SidebarLinkProps) {
  return (
    <Tooltip content={label} side="right" disabled={!rail}>
      <NavLink
        to={to}
        end={end}
        aria-label={rail ? label : undefined}
        className={({ isActive }) =>
          clsx(
            "group relative isolate flex h-9 items-center gap-3 rounded-control px-2.5 text-[13.5px] font-medium transition-colors duration-150 pointer-coarse:h-11",
            "md:justify-center lg:justify-start",
            isActive ? "text-text" : "text-muted hover:bg-surface-2/70 hover:text-text",
          )
        }
      >
        {({ isActive }) => (
          <>
            {isActive ? (
              <>
                <motion.span
                  layoutId="rr-sidebar-active"
                  transition={SPRING}
                  aria-hidden="true"
                  className="absolute inset-0 -z-10 rounded-control border border-border bg-surface-2 shadow-[var(--inner-highlight)]"
                />
                <motion.span
                  layoutId="rr-sidebar-active-bar"
                  transition={SPRING}
                  aria-hidden="true"
                  className="absolute top-1/2 -left-3 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-accent shadow-[0_0_12px_var(--accent)]"
                />
              </>
            ) : null}
            <Icon
              aria-hidden="true"
              className={clsx("size-[18px] shrink-0 transition-colors", isActive ? "text-accent" : "text-faint group-hover:text-muted")}
            />
            <span className="truncate md:sr-only lg:not-sr-only">{label}</span>
          </>
        )}
      </NavLink>
    </Tooltip>
  );
}

function SectionLabel({ children }: { children: string }) {
  return (
    <p aria-hidden="true" className="mb-1.5 px-2.5 text-eyebrow md:hidden lg:block">
      {children}
    </p>
  );
}

/**
 * Desktop navigation (UI.md §4): 248 px sidebar ≥ 1024 px, 72 px icon rail with tooltips at
 * 768–1023 px, hidden below 768 px (mobile top bar + bottom tabs take over).
 */
export function Sidebar({ projectId }: { projectId: number | null }) {
  const wide = useMediaQuery(WIDE_SIDEBAR_QUERY);
  const rail = !wide;
  return (
    <aside
      aria-label="Sidebar"
      className="sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-border bg-[color-mix(in_oklab,var(--surface)_55%,var(--bg))] md:flex md:w-[72px] lg:w-[248px]"
    >
      <div className="flex h-16 shrink-0 items-center px-4 md:justify-center lg:justify-start lg:px-5">
        <Link
          to={paths.root()}
          aria-label="Ratiocination home"
          className="flex items-center rounded-control p-1 -m-1 text-text transition-opacity duration-150 hover:opacity-80"
        >
          {/* Icon rail (768–1023 px): the R mark; full sidebar: the lockup (mark + wordmark). */}
          <BrandMark size={30} className="lg:hidden" />
          <BrandLockup height={22} className="hidden lg:block" />
        </Link>
      </div>

      <div className={clsx("shrink-0 px-3", rail && "flex justify-center")}>
        <ProjectSwitcher projectId={projectId} variant={rail ? "rail" : "sidebar"} />
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-x-hidden overflow-y-auto px-3 pt-5 pb-4">
        {projectId !== null ? (
          <nav aria-label="Project" className="flex flex-col gap-0.5">
            <SectionLabel>Project</SectionLabel>
            {PROJECT_NAV.map((item) => (
              <SidebarLink key={item.key} to={item.to(projectId)} label={item.label} icon={item.icon} rail={rail} />
            ))}
          </nav>
        ) : (
          <div className="hidden rounded-button border border-dashed border-border-strong px-3 py-3 text-[12.5px] text-muted lg:block">
            Pick or create a project to see its feed, library and graph.
          </div>
        )}
        <nav aria-label="Workspace" className="flex flex-col gap-0.5">
          <SectionLabel>Workspace</SectionLabel>
          {GLOBAL_NAV.map((item) => (
            <SidebarLink key={item.key} to={item.to} label={item.label} icon={item.icon} end={item.key === "projects"} rail={rail} />
          ))}
        </nav>
      </div>

      <div className={clsx("flex shrink-0 flex-col gap-2.5 border-t border-border p-3", rail ? "items-center" : "items-stretch")}>
        {rail ? <ThemeCycleButton tooltipSide="right" /> : <ThemeSegmented />}
        <PrivacyBadge compact={rail} />
        <UserMenu variant={rail ? "rail" : "sidebar"} />
      </div>
    </aside>
  );
}
