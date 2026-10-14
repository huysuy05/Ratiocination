import { TriangleAlert } from "lucide-react";
import { isRouteErrorResponse, Link, useRouteError } from "react-router";
import { Button } from "../components/ui/Button";
import { BrandMark } from "../components/ui/BrandMark";
import { paths } from "./paths";

/** Shown while the first route (and its lazy page) loads. */
export function AppLoading() {
  return (
    <div role="status" aria-live="polite" className="grid min-h-dvh place-items-center bg-bg px-4 text-muted">
      <div className="flex flex-col items-center gap-4 animate-fade-in">
        <BrandMark size={44} className="text-text" />
        <span className="text-sm">Loading Ratiocination…</span>
      </div>
    </div>
  );
}

/** Route error boundary: unexpected render/loader errors and unknown routes. */
export function RouteError() {
  const error = useRouteError();
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : "Unknown error";
  return (
    <div className="relative isolate grid min-h-dvh place-items-center overflow-hidden bg-bg px-4 py-10 text-text">
      <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[520px] app-backdrop" />
      <div
        role="alert"
        className="flex w-full max-w-lg flex-col gap-4 rounded-card border border-border bg-surface p-6 surface-sheen elevation-card md:p-8"
      >
        <div className="grid size-11 place-items-center rounded-full bg-not-relevant/12 text-not-relevant ring-1 ring-not-relevant/25">
          <TriangleAlert aria-hidden="true" className="size-5" />
        </div>
        <h1 className="font-serif text-2xl font-semibold tracking-[-0.01em]">{notFound ? "Page not found" : "Something went wrong"}</h1>
        <p className="text-muted">
          {notFound ? "This page does not exist." : "Ratiocination hit an unexpected error while showing this page."}
        </p>
        {!notFound && (
          <p className="rounded-control border border-border bg-surface-2 px-3 py-2 font-mono text-xs break-words text-faint">{message}</p>
        )}
        <div className="mt-2 flex flex-wrap gap-3">
          <Button variant="primary" onClick={() => window.location.reload()}>
            Reload
          </Button>
          <Button asChild variant="secondary">
            <Link to={paths.projects()}>Go to projects</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
