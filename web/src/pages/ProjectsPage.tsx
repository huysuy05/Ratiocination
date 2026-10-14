/** Projects (UI.md §10): a grid of project cards, or the first-project empty state. */
import { motion } from "motion/react";
import { Bookmark, Check, FileText, Layers3, Plus } from "lucide-react";
import { Link } from "react-router";
import { useProjects } from "../api/hooks";
import type { Project } from "../api/types";
import { paths } from "../app/paths";
import { ProjectAvatar } from "../app/shell/ProjectSwitcher";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { PageHeader } from "../components/ui/PageHeader";
import { RadarScope } from "../components/ui/RadarSweep";
import { Skeleton } from "../components/ui/Skeleton";
import { categoryLabel } from "../features/projects/categories";
import { formatRelativeDate } from "../lib/format";
import { staggerDelay, useMotionPreference } from "../lib/motion";

function Stat({ icon: Icon, value, label }: { icon: typeof Check; value: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-muted" title={label}>
      <Icon aria-hidden="true" className="size-3.5 text-faint" />
      <span className="font-medium text-text tabular-nums">{value}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

function ProjectCard({ project, index }: { project: Project; index: number }) {
  const { reduced } = useMotionPreference();
  const target = project.active ? paths.feed(project.id) : paths.projectSettings(project.id);
  return (
    <motion.li
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, delay: staggerDelay(index, reduced) }}
    >
      <Link
        to={target}
        className="group relative flex h-full flex-col gap-4 overflow-hidden rounded-card border border-border bg-surface p-5 surface-sheen elevation-card transition-[border-color,transform,box-shadow] duration-200 hover:border-accent/40 full-motion:hover:-translate-y-0.5"
      >
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -top-20 -right-20 size-48 rounded-full bg-accent/0 blur-3xl transition-colors duration-300 group-hover:bg-accent/10"
        />
        <div className="flex items-start gap-3">
          <ProjectAvatar project={project} />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[16px] font-semibold text-text">{project.name}</h2>
            <p className="text-[12.5px] text-muted">
              {project.stats.last_deck_at ? `Deck ${formatRelativeDate(project.stats.last_deck_at)}` : "No deck yet"}
            </p>
          </div>
          {!project.active ? <Badge variant="dashed">Draft</Badge> : null}
          {project.active && project.stats.remaining_in_deck > 0 ? (
            <Badge tone="accent" glow>
              {project.stats.remaining_in_deck} new
            </Badge>
          ) : null}
        </div>
        <p className="line-clamp-3 font-serif text-[15px] leading-relaxed text-muted">{project.research_question}</p>
        <div className="mt-auto flex flex-wrap gap-1.5">
          {project.settings.arxiv_categories.slice(0, 3).map((code) => (
            <span key={code} className="rounded-pill border border-border px-2 py-0.5 text-[11.5px] text-muted" title={categoryLabel(code)}>
              {code}
            </span>
          ))}
          {project.settings.arxiv_categories.length > 3 ? (
            <span className="px-1 text-[11.5px] text-faint">+{project.settings.arxiv_categories.length - 3}</span>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border pt-3">
          <Stat icon={Check} value={project.stats.relevant} label="relevant" />
          <Stat icon={Bookmark} value={project.stats.saved} label="saved" />
          <Stat icon={Layers3} value={project.stats.seeds} label="seed papers" />
          <Stat icon={FileText} value={project.stats.documents} label="documents" />
        </div>
      </Link>
    </motion.li>
  );
}

export default function ProjectsPage() {
  const projects = useProjects();
  const newProject = (
    <Button asChild variant="primary" leadingIcon={<Plus aria-hidden="true" />}>
      <Link to={paths.newProject()}>New project</Link>
    </Button>
  );

  return (
    <section aria-labelledby="page-title" className="mx-auto w-full max-w-6xl">
      <PageHeader
        title="Projects"
        description="Each project follows one research question, with its own deck, library and graph."
        actions={newProject}
      />
      {projects.isPending ? (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <li key={i}>
              <Skeleton className="h-56 rounded-card" />
            </li>
          ))}
        </ul>
      ) : projects.isError ? (
        <EmptyState title="Could not load your projects" description={projects.error.message} actions={<Button onClick={() => void projects.refetch()}>Try again</Button>} />
      ) : projects.data.length === 0 ? (
        <EmptyState
          size="lg"
          icon={<RadarScope size="sm" />}
          title="Create your first project"
          description="Tell Ratiocination what you are working on. Every morning it lines up the new papers that matter, and learns from your swipes."
          actions={newProject}
        />
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {projects.data.map((project, index) => (
            <ProjectCard key={project.id} project={project} index={index} />
          ))}
        </ul>
      )}
    </section>
  );
}
