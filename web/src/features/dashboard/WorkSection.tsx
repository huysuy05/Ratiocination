/**
 * Relation to your work (UI.md §12.5), per project: does closeness to your question/draft predict
 * what you keep (correlation + relevant rate per similarity quartile), the closest papers and what
 * they matched, which draft sections are supported by your library, and the 12-week drift.
 */
import clsx from "clsx";
import { motion } from "motion/react";
import { FileText, MessageSquareQuote, Target } from "lucide-react";
import type { DashboardWork, WorkProject, WorkQuartile } from "../../api/types";
import { Sparkline } from "../../components/charts/Sparkline";
import { Skeleton } from "../../components/ui/Skeleton";
import { useMotionPreference } from "../../lib/motion";
import { percent, shortDate } from "./format";
import { Panel } from "./Panel";

const STRENGTH_COLOR = { none: "var(--text-muted)", weak: "var(--save)", moderate: "var(--accent)", strong: "var(--relevant)" } as const;

function QuartileBars({ quartiles }: { quartiles: readonly WorkQuartile[] }) {
  const { reduced } = useMotionPreference();
  const names = ["Least similar", "", "", "Most similar"];
  return (
    <figure className="flex flex-col gap-2">
      <div className="flex h-32 items-end gap-3" role="img" aria-label={`Relevant rate by similarity quartile: ${quartiles.map((q) => percent(q.positive_rate)).join(", ")}`}>
        {quartiles.map((quartile, index) => (
          <div key={index} className="flex h-full flex-1 flex-col items-center justify-end gap-1">
            <span className="text-[11.5px] font-medium text-text tabular-nums">{percent(quartile.positive_rate)}</span>
            <motion.span
              className="w-full rounded-t-[6px] bg-gradient-to-t from-accent/40 to-accent"
              initial={reduced ? false : { height: 0 }}
              animate={{ height: `${Math.max(4, (quartile.positive_rate ?? 0) * 100)}%` }}
              transition={{ duration: 0.6, delay: reduced ? 0 : index * 0.08, ease: [0.22, 1, 0.36, 1] }}
            />
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[11px] text-faint">
        {names.map((name, index) => (
          <span key={index}>{name}</span>
        ))}
      </div>
      <figcaption className="text-[12px] text-muted">Share kept (relevant or saved) in each quarter of the papers you swiped, by closeness to your work.</figcaption>
    </figure>
  );
}

function ProjectWork({ project, onOpenPaper }: { project: WorkProject; onOpenPaper: (id: number) => void }) {
  const drift = project.drift.map((point) => point.mean_similarity);
  const driftKnown = drift.filter((value) => value !== null).length;
  const sections = project.sections;
  const maxSupport = Math.max(1, ...sections.map((section) => section.n_supporting));
  return (
    <Panel
      title={project.name}
      subtitle={project.has_documents ? "Compared with your research question and your draft" : "Compared with your research question — upload a draft to compare section by section"}
      icon={<Target aria-hidden="true" />}
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
          {project.correlation ? (
            <p className="text-[15px] leading-relaxed text-text">
              {project.correlation.text}.{" "}
              <span className="text-[12px] font-semibold uppercase" style={{ color: STRENGTH_COLOR[project.correlation.strength] }}>
                {project.correlation.strength === "none" ? "no link" : `${project.correlation.strength} link`}
              </span>
            </p>
          ) : (
            <p className="text-[13.5px] text-muted">Rate at least 10 papers — some relevant, some not — to see whether closeness to your work predicts what you keep.</p>
          )}
          {project.quartiles.length === 4 ? <QuartileBars quartiles={project.quartiles} /> : null}
          {driftKnown > 1 ? (
            <div className="flex items-center gap-3 text-[12.5px] text-muted">
              <Sparkline values={drift} label="Weekly average closeness of your liked papers to your question" width={140} height={34} />
              <span>
                Closeness of what you keep to your question, 12 weeks{project.drift[0] ? ` from ${shortDate(project.drift[0].week)}` : ""}
              </span>
            </div>
          ) : null}
        </div>
        <section className="flex flex-col gap-2">
          <h3 className="text-[13px] font-semibold text-text">Closest to your work</h3>
          {project.closest.length === 0 ? (
            <p className="text-[13px] text-muted">Keep papers in this project to see which ones are closest.</p>
          ) : (
            <ol className="flex flex-col gap-2">
              {project.closest.map((item) => (
                <li key={item.paper.id} className="flex items-start gap-3 rounded-control border border-border bg-surface-2/40 px-3 py-2.5">
                  <span className="mt-0.5 w-10 shrink-0 text-right font-mono text-[12px] text-accent">{percent(item.similarity)}</span>
                  <span className="min-w-0 flex-1">
                    <button type="button" onClick={() => onOpenPaper(item.paper.id)} className="text-left font-serif text-[14px] leading-snug text-text hover:text-accent">
                      {item.paper.title}
                    </button>
                    <span className="mt-0.5 flex items-center gap-1 text-[11.5px] text-muted">
                      {item.match.kind === "question" ? (
                        <>
                          <MessageSquareQuote aria-hidden="true" className="size-3" />
                          your research question
                        </>
                      ) : (
                        <>
                          <FileText aria-hidden="true" className="size-3" />
                          <span className="truncate" title={item.match.snippet}>
                            {item.match.filename} · {item.match.section}
                            {item.match.page ? ` · p. ${item.match.page}` : ""}
                          </span>
                        </>
                      )}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
      {sections.length > 0 ? (
        <section className="flex flex-col gap-2 border-t border-border pt-4">
          <h3 className="text-[13px] font-semibold text-text">Your draft, section by section</h3>
          <p className="text-[12px] text-muted">Papers in your library whose closest passage is in each section (cosine ≥ {project.threshold.toFixed(2)}).</p>
          <ul className="flex flex-col gap-1.5">
            {sections.map((section) => (
              <li key={section.section_id} className="flex items-center gap-3 text-[13px]">
                <span className={clsx("w-48 truncate", section.n_supporting === 0 ? "text-muted" : "text-text")} title={section.heading}>
                  {section.heading}
                </span>
                <span className="relative h-2 flex-1 overflow-hidden rounded-pill bg-surface-3">
                  <span className="absolute inset-y-0 left-0 rounded-pill bg-accent" style={{ width: `${(section.n_supporting / maxSupport) * 100}%` }} />
                </span>
                {section.n_supporting === 0 ? (
                  <span className="w-48 text-right text-[12px] text-save">No supporting paper yet</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => section.best && onOpenPaper(section.best.paper.id)}
                    className="w-48 truncate text-right text-[12px] text-muted hover:text-accent"
                    title={section.best?.paper.title}
                  >
                    {section.n_supporting} · best: {section.best?.paper.title}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Panel>
  );
}

export function WorkSection({ work, loading, onOpenPaper }: { work: DashboardWork | undefined; loading: boolean; onOpenPaper: (id: number) => void }) {
  if (loading || !work) return <Skeleton className="h-[360px] rounded-card" />;
  if (work.projects.length === 0) return null;
  return (
    <div className="flex flex-col gap-4">
      <h2 className="mt-2 text-[13px] font-semibold tracking-[0.08em] text-faint uppercase">Relation to your work</h2>
      {work.projects.map((project) => (
        <ProjectWork key={project.project_id} project={project} onOpenPaper={onOpenPaper} />
      ))}
    </div>
  );
}
