/** A paper on its own page (`/papers/:paperId?project=`), with the same content as the sheet. */
import { ArrowLeft } from "lucide-react";
import { Link, useNavigate } from "react-router";
import { usePaper, useProject } from "../api/hooks";
import { usePaperIdParam, useProjectSearchParam } from "../app/params";
import { paths } from "../app/paths";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { Skeleton, SkeletonText } from "../components/ui/Skeleton";
import { PaperActions } from "../features/paper/PaperActions";
import { PaperDetailContent } from "../features/paper/PaperDetailContent";

export default function PaperPage() {
  const paperId = usePaperIdParam();
  const projectId = useProjectSearchParam();
  const paper = usePaper(paperId, projectId);
  const project = useProject(projectId);
  const navigate = useNavigate();

  if (paperId === null) return null;
  const back = projectId !== null ? (
    <Link to={paths.library(projectId)} className="mb-4 inline-flex items-center gap-1.5 text-[13px] font-medium text-muted hover:text-text">
      <ArrowLeft aria-hidden="true" className="size-4" />
      {project.data ? `${project.data.name} · Library` : "Library"}
    </Link>
  ) : null;

  return (
    <section aria-labelledby="page-title" className="mx-auto w-full max-w-3xl pb-24">
      {back}
      {paper.isPending ? (
        <Card padding="lg" className="flex flex-col gap-4">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-20" />
          <SkeletonText lines={8} />
        </Card>
      ) : paper.isError ? (
        <EmptyState title="Paper not found" description={paper.error.message} />
      ) : (
        <>
          <Card padding="lg">
            <PaperDetailContent
              paper={paper.data}
              projectId={projectId}
              headingLevel="h1"
              onOpenPaper={(id) => void navigate(paths.paper(id, projectId))}
            />
          </Card>
          {projectId !== null ? (
            <div className="glass sticky bottom-20 z-20 mt-4 rounded-panel border border-border px-4 py-3 elevation-popover md:bottom-4">
              <PaperActions paper={paper.data} projectId={projectId} source="detail" />
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
