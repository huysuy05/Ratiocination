/** A compact library card (UI.md §11): state, read mark, title, byline, excerpt and chips. */
import clsx from "clsx";
import { Eye, EyeOff } from "lucide-react";
import { motion } from "motion/react";
import { useSetRead } from "../../api/hooks";
import type { LibraryItem } from "../../api/types";
import { IconButton } from "../../components/ui/IconButton";
import { formatAuthors, formatRelativeDate } from "../../lib/format";
import { staggerDelay, useMotionPreference } from "../../lib/motion";
import { STATE_META } from "../../lib/states";
import { PaperChips } from "../paper/meta";

export function LibraryCard({ item, index, onOpen }: { item: LibraryItem; index: number; onOpen: () => void }) {
  const { reduced } = useMotionPreference();
  const setRead = useSetRead();
  const meta = STATE_META[item.state];
  const StateIcon = meta.icon;
  const read = item.paper.read_at !== null;
  return (
    <motion.li
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: staggerDelay(index % 10, reduced) }}
      className={clsx(
        "group relative flex flex-col gap-3 rounded-card border border-border bg-surface p-5 surface-sheen elevation-card transition-[border-color,transform] duration-200",
        "hover:border-border-strong full-motion:hover:-translate-y-0.5",
        read && "opacity-80",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-[12px] font-medium" style={{ color: meta.color }}>
          <StateIcon aria-hidden="true" className="size-3.5" />
          {meta.label}
        </span>
        <span className="text-[12px] text-faint">· {formatRelativeDate(item.updated_at)}</span>
        <IconButton
          label={read ? "Mark as unread" : "Mark as read"}
          icon={read ? <Eye /> : <EyeOff />}
          variant="ghost"
          size="sm"
          aria-pressed={read}
          className={clsx("relative z-10 ml-auto", read ? "text-read" : "text-faint")}
          onClick={() => setRead.mutate({ paperId: item.paper.id, read: !read })}
        />
      </div>
      <h2 className="font-serif text-[17px] leading-snug font-semibold text-text">
        <button type="button" onClick={onOpen} className="text-left after:absolute after:inset-0 after:rounded-card hover:text-accent focus-visible:outline-none">
          {item.paper.title}
        </button>
      </h2>
      <p className="-mt-1.5 truncate text-[12.5px] text-muted">
        {[formatAuthors(item.paper.authors, 2), item.paper.year].filter(Boolean).join(" · ")}
      </p>
      {item.paper.abstract ? <p className="line-clamp-3 text-[13.5px] leading-relaxed text-muted">{item.paper.abstract}</p> : null}
      <PaperChips paper={{ ...item.paper, read_at: null }} className="mt-auto" />
    </motion.li>
  );
}
