/** Private documents of a project (UI.md §10). */
import { useProject } from "../api/hooks";
import { useProjectIdParam } from "../app/params";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { DocumentDropzone, DocumentList } from "../features/documents/DocumentTools";

export default function DocumentsPage() {
  const projectId = useProjectIdParam();
  const project = useProject(projectId);
  if (projectId === null) return null;
  return (
    <section aria-labelledby="page-title" className="mx-auto w-full max-w-3xl">
      <PageHeader
        eyebrow={project.data?.name}
        title="Documents"
        description="Upload your draft, proposal or notes. Ratiocination matches new papers against its sections and tells you which part of your work each paper speaks to."
      />
      <div className="flex flex-col gap-6">
        <Card padding="md">
          <DocumentDropzone projectId={projectId} />
        </Card>
        <DocumentList projectId={projectId} />
      </div>
    </section>
  );
}
