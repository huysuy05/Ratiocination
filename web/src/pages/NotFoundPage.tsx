import { Link } from "react-router";
import { paths } from "../app/paths";

/** Unknown routes (UI.md §4, owner: foundation). */
export default function NotFoundPage() {
  return (
    <section aria-labelledby="page-title" className="mx-auto flex max-w-3xl flex-col gap-3 py-10">
      <h1 id="page-title" className="font-serif text-2xl text-text">
        Page not found
      </h1>
      <p className="text-muted">There is nothing at this address.</p>
      <p>
        <Link to={paths.projects()} className="text-accent underline underline-offset-4">
          Go to your projects
        </Link>
      </p>
    </section>
  );
}
