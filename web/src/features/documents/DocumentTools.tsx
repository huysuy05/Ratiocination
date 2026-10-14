/**
 * Private documents (UI.md §10): a dropzone with the privacy note and upload progress, and the list
 * of documents with status, outline and delete. Shared by the wizard and the Documents page.
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, CircleAlert, FileText, LockKeyhole, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { toast } from "sonner";
import { errorMessage, getConflictDocumentId, isApiError } from "../../api/client";
import { useDeleteDocument, useDocuments, useUploadDocument } from "../../api/hooks";
import type { DocumentInfo } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { IconButton } from "../../components/ui/IconButton";
import { ProgressBar } from "../../components/ui/ProgressBar";
import { Skeleton } from "../../components/ui/Skeleton";
import { formatBytes, formatRelativeDate } from "../../lib/format";
import { SPRING } from "../../lib/motion";

const ACCEPT = ".pdf,.md,.txt,application/pdf,text/markdown,text/plain";

function uploadErrorText(error: unknown): string {
  if (getConflictDocumentId(error) !== null) return "This file is already in the project.";
  if (isApiError(error) && error.status === 415) return "Upload a PDF, a .txt or a .md file.";
  if (isApiError(error) && error.status === 413) return error.message || "This file is too large.";
  return errorMessage(error, "The upload failed.");
}

export function DocumentDropzone({ projectId, compact = false }: { projectId: number; compact?: boolean }) {
  const upload = useUploadDocument(projectId);
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = (file: File | undefined) => {
    if (!file || upload.isPending) return;
    setError(null);
    setProgress(0);
    upload.mutate(
      { file, onProgress: setProgress },
      {
        onSuccess: ({ document }) => toast.success(`${document.filename} uploaded — processing on the server`),
        onError: (err) => setError(uploadErrorText(err)),
        onSettled: () => setProgress(null),
      },
    );
  };

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setDragging(false);
    send(event.dataTransfer.files[0]);
  };

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        disabled={upload.isPending}
        className={clsx(
          "group relative flex w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-card border-2 border-dashed text-center transition-colors",
          compact ? "px-5 py-6" : "px-6 py-10",
          dragging ? "border-accent bg-accent/8" : "border-border-strong bg-surface-2/40 hover:border-accent/60 hover:bg-surface-2",
          "disabled:cursor-progress",
        )}
      >
        <span className="grid size-11 place-items-center rounded-full bg-accent/12 text-accent ring-1 ring-accent/25 transition-transform duration-200 full-motion:group-hover:-translate-y-0.5">
          <UploadCloud aria-hidden="true" className="size-5" />
        </span>
        <span className="text-[14px] font-medium text-text">
          {upload.isPending ? "Uploading…" : "Drop your draft here, or click to choose"}
        </span>
        <span className="text-[12.5px] text-muted">PDF, Markdown or plain text</span>
        {progress !== null ? <ProgressBar value={progress} label="Upload progress" className="mt-2 w-full max-w-xs" /> : null}
      </button>
      <input ref={input} type="file" accept={ACCEPT} className="sr-only" tabIndex={-1} onChange={(event) => send(event.target.files?.[0])} />
      <p className="flex items-start gap-2 text-[12.5px] text-muted">
        <LockKeyhole aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-accent" />
        Processed on this server. The text never leaves it and is used only to rank papers for this project.
      </p>
      {error ? (
        <p role="alert" className="flex items-center gap-1.5 text-[13px] font-medium text-not-relevant">
          <CircleAlert aria-hidden="true" className="size-4" />
          {error}
        </p>
      ) : null}
    </div>
  );
}

const STATUS_LABEL: Record<DocumentInfo["status"], string> = {
  uploaded: "Queued",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

function DocumentRow({ document, projectId }: { document: DocumentInfo; projectId: number }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const remove = useDeleteDocument(projectId);
  const pending = document.status === "uploaded" || document.status === "processing";
  const tone = document.status === "ready" ? "relevant" : document.status === "failed" ? "not-relevant" : "accent";

  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, x: -16 }}
      transition={SPRING}
      className="overflow-hidden rounded-panel border border-border bg-surface elevation-card"
    >
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-control bg-surface-2 text-muted">
          <FileText aria-hidden="true" className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[14px] font-medium text-text">{document.filename}</p>
          <p className="truncate text-[12.5px] text-muted">
            {[
              formatBytes(document.size_bytes),
              document.n_pages ? `${document.n_pages} pages` : null,
              document.status === "ready" ? `${document.n_sections} sections · ${document.n_chunks} passages` : null,
              formatRelativeDate(document.created_at),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <Badge tone={tone} variant={pending ? "dashed" : "soft"} className={pending ? "animate-pulse" : undefined}>
          {STATUS_LABEL[document.status]}
        </Badge>
        {document.status === "ready" && document.outline.length > 0 ? (
          <IconButton
            label={open ? "Hide outline" : "Show outline"}
            icon={<ChevronDown className={clsx("transition-transform", open && "rotate-180")} />}
            variant="ghost"
            size="sm"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          />
        ) : null}
        <IconButton
          label={pending ? "Wait until processing finishes to delete" : `Delete ${document.filename}`}
          icon={<Trash2 />}
          variant="ghost"
          size="sm"
          disabled={pending}
          onClick={() => setConfirming(true)}
        />
      </div>
      {pending ? (
        <div className="flex flex-col gap-1.5 border-t border-border bg-surface-2/50 px-4 py-2.5">
          <ProgressBar value={document.progress} label="Processing" size="sm" />
          <p className="text-[12.5px] text-muted">{document.message ?? "Waiting for the processor…"}</p>
        </div>
      ) : null}
      {document.status === "failed" && document.error ? (
        <p className="border-t border-border bg-not-relevant/6 px-4 py-2.5 text-[13px] text-not-relevant">{document.error}</p>
      ) : null}
      <AnimatePresence initial={false}>
        {open ? (
          <motion.ol
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-border bg-surface-2/40"
          >
            {document.outline.map((item, index) => (
              <li key={`${item.heading}-${index}`} className="flex items-center gap-3 px-4 py-1.5 text-[13px]">
                <span className="min-w-0 flex-1 truncate text-text">{item.heading}</span>
                {item.page_start ? <span className="text-faint tabular-nums">p. {item.page_start}</span> : null}
                <span className="text-faint tabular-nums">{item.n_chunks} passages</span>
              </li>
            ))}
          </motion.ol>
        ) : null}
      </AnimatePresence>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        tone="danger"
        title={`Delete ${document.filename}?`}
        description="The file, its extracted text, passages and their vectors are deleted from this server. Deck reasons that quoted it will say the document was deleted."
        confirmLabel="Delete document"
        pending={remove.isPending}
        onConfirm={() =>
          remove.mutateAsync(document.id).then(
            (result) => {
              setConfirming(false);
              toast.success(
                `Deleted ${document.filename} (${result.deleted.chunks} passages, ${result.deleted.embeddings} vectors)`,
              );
            },
            (error: unknown) => toast.error(errorMessage(error, "Could not delete the document")),
          )
        }
      />
    </motion.li>
  );
}

export function DocumentList({ projectId }: { projectId: number }) {
  const documents = useDocuments(projectId);
  if (documents.isPending) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
    );
  }
  if (documents.isError) return <p className="text-[13px] text-not-relevant">{documents.error.message}</p>;
  if (documents.data.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2.5">
      <AnimatePresence initial={false}>
        {documents.data.map((document) => (
          <DocumentRow key={document.id} document={document} projectId={projectId} />
        ))}
      </AnimatePresence>
    </ul>
  );
}
