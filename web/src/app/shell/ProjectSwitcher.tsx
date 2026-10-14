import clsx from "clsx";
import { ChevronsUpDown, FolderKanban, Plus } from "lucide-react";
import { useMatch, useNavigate } from "react-router";
import { parseId, useProjects } from "../../api/hooks";
import type { Project } from "../../api/types";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuSeparator, MenuTrigger } from "../../components/ui/Menu";
import { Skeleton } from "../../components/ui/Skeleton";
import { Tooltip } from "../../components/ui/Tooltip";
import { paths } from "../paths";

/* Avatar colours cycle through the feature palette (accessible hues per theme). */
export const AVATAR_COLORS = [
  "var(--feat-question)",
  "var(--feat-anchor)",
  "var(--feat-draft)",
  "var(--feat-citation)",
  "var(--consolidating)",
  "var(--feat-preference)",
  "var(--disruptive)",
  "var(--feat-recency)",
] as const;

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.length >= 2 ? `${words[0]?.[0] ?? ""}${words[1]?.[0] ?? ""}` : (words[0] ?? "").slice(0, 2);
  return letters.toUpperCase() || "?";
}

export function ProjectAvatar({ project, size = "md", className }: { project: Pick<Project, "id" | "name"> | null; size?: "sm" | "md"; className?: string }) {
  const color = project ? AVATAR_COLORS[project.id % AVATAR_COLORS.length] : "var(--text-faint)";
  return (
    <span
      aria-hidden="true"
      style={{ backgroundColor: color }}
      className={clsx(
        "grid shrink-0 place-items-center font-semibold tracking-tight text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.25),inset_0_-8px_16px_-8px_rgba(0,0,0,0.25)]",
        size === "md" ? "size-8 rounded-[9px] text-[12px]" : "size-6 rounded-[7px] text-[10px]",
        className,
      )}
    >
      {project ? initials(project.name) : <FolderKanban className="size-3.5" />}
    </span>
  );
}

/** Where switching to another project goes: the same section when on a project page, else its feed. */
function useSectionPath(): (projectId: number) => string {
  const section = useMatch("/projects/:projectId/:section/*")?.params.section;
  return (projectId: number) => {
    switch (section) {
      case "library":
        return paths.library(projectId);
      case "graph":
        return paths.graph(projectId);
      case "documents":
        return paths.documents(projectId);
      case "settings":
        return paths.projectSettings(projectId);
      default:
        return paths.feed(projectId);
    }
  };
}

export type ProjectSwitcherVariant = "sidebar" | "rail" | "topbar";

/**
 * Project switcher (UI.md §4): a dropdown of projects plus "All projects" and "New project".
 * `sidebar` = full width with name; `rail` = avatar only (tooltip); `topbar` = compact (mobile).
 */
export function ProjectSwitcher({ projectId, variant = "sidebar", className }: { projectId: number | null; variant?: ProjectSwitcherVariant; className?: string }) {
  const { data: projects, isPending, isError } = useProjects();
  const navigate = useNavigate();
  const sectionPath = useSectionPath();
  const current = projectId !== null ? (projects?.find((project) => project.id === projectId) ?? null) : null;
  const name = current?.name ?? (projectId !== null && isPending ? "Loading…" : "Choose a project");
  const remaining = current?.stats.remaining_in_deck ?? 0;
  const subtitle = current ? (remaining > 0 ? `${remaining} ${remaining === 1 ? "card" : "cards"} in today's deck` : "Research project") : "No project selected";
  const ariaLabel = current ? `${current.name}, switch project` : "Choose a project";

  const trigger =
    variant === "rail" ? (
      <button
        type="button"
        aria-label={ariaLabel}
        className={clsx("relative grid size-10 place-items-center rounded-button transition-colors hover:bg-surface-2", className)}
      >
        <ProjectAvatar project={current} />
      </button>
    ) : (
      <button
        type="button"
        aria-label={ariaLabel}
        className={clsx(
          "group flex min-w-0 items-center gap-2.5 text-left transition-[background-color,border-color] duration-150 data-[state=open]:border-border-strong data-[state=open]:bg-surface-2",
          variant === "sidebar"
            ? "w-full rounded-button border border-border bg-surface/70 p-2 shadow-[var(--inner-highlight)] hover:border-border-strong hover:bg-surface-2"
            : "h-11 rounded-button px-2 hover:bg-surface-2",
          className,
        )}
      >
        <ProjectAvatar project={current} size={variant === "topbar" ? "sm" : "md"} />
        <span className="min-w-0 flex-1 leading-tight">
          <span className={clsx("block truncate font-semibold text-text", variant === "topbar" ? "text-[14px]" : "text-[13.5px]")}>{name}</span>
          {variant === "sidebar" ? <span className="block truncate text-[11.5px] text-muted">{subtitle}</span> : null}
        </span>
        <ChevronsUpDown aria-hidden="true" className="size-4 shrink-0 text-faint group-hover:text-muted" />
      </button>
    );

  return (
    <Menu>
      {variant === "rail" ? (
        <Tooltip content={current ? `${current.name} · Switch project` : "Choose a project"} side="right">
          <MenuTrigger asChild>{trigger}</MenuTrigger>
        </Tooltip>
      ) : (
        <MenuTrigger asChild>{trigger}</MenuTrigger>
      )}
      <MenuContent align="start" side={variant === "rail" ? "right" : "bottom"} className="w-[min(18rem,calc(100vw-1.5rem))]">
        <MenuLabel>Projects</MenuLabel>
        {isPending ? (
          <div className="flex flex-col gap-2 px-2.5 py-2" aria-hidden="true">
            <Skeleton shape="text" className="w-40" />
            <Skeleton shape="text" className="w-28" />
          </div>
        ) : isError ? (
          <p className="px-2.5 py-2 text-[13px] text-muted">Could not load projects.</p>
        ) : projects && projects.length > 0 ? (
          <MenuRadioGroup
            value={current ? String(current.id) : ""}
            onValueChange={(value) => {
              const id = parseId(value);
              if (id !== null) void navigate(sectionPath(id));
            }}
          >
            {projects.map((project) => (
              <MenuRadioItem key={project.id} value={String(project.id)} icon={<ProjectAvatar project={project} size="sm" />}>
                {project.name}
              </MenuRadioItem>
            ))}
          </MenuRadioGroup>
        ) : (
          <p className="px-2.5 py-2 text-[13px] text-muted">No projects yet.</p>
        )}
        <MenuSeparator />
        <MenuItem icon={<FolderKanban aria-hidden="true" />} onSelect={() => void navigate(paths.projects())}>
          All projects
        </MenuItem>
        <MenuItem icon={<Plus aria-hidden="true" />} onSelect={() => void navigate(paths.newProject())}>
          New project
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
