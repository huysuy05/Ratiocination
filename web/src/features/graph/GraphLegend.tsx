/**
 * Always-visible legend under the canvas (UI.md §9): line styles carry each relation's meaning, not
 * colour alone. It sits outside the canvas so it never covers part of the graph.
 */
import { RELATIONS, RELATION_TEXT } from "./model";

const DASH: Record<string, string | undefined> = { SEMANTICALLY_SIMILAR: "6 4", HAS_TOPIC: "1.5 3" };

export function GraphLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-1 text-[12px] text-muted">
      <ul aria-label="Links" className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {RELATIONS.map((relation) => (
          <li key={relation} className="flex items-center gap-2">
            <svg width="30" height="8" aria-hidden="true">
              <line
                x1="1"
                y1="4"
                x2={relation === "CITES" ? 23 : 29}
                y2="4"
                stroke={RELATION_TEXT[relation].color}
                strokeWidth={relation === "AUTHORED" ? 1.2 : 2}
                strokeDasharray={DASH[relation]}
                strokeLinecap="round"
              />
              {relation === "CITES" ? <path d="M23 1 L29 4 L23 7 Z" fill={RELATION_TEXT[relation].color} /> : null}
            </svg>
            {RELATION_TEXT[relation].legend}
          </li>
        ))}
      </ul>
      <span aria-hidden="true" className="h-4 w-px bg-border max-sm:hidden" />
      <ul aria-label="Nodes" className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5">
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-full bg-relevant" />
          relevant
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-full bg-save" />
          saved
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-full border-2 border-accent" />
          not rated
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rotate-45 bg-edge-authored" />
          author
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2 w-3 rounded-[3px] border border-dotted border-edge-topic" />
          topic
        </li>
      </ul>
    </div>
  );
}
