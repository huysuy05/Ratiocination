/** Route/search-param helpers shared by pages (UI.md §4–5). */
import { useCallback, useMemo } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { parseId } from "../api/hooks";

/** `:projectId` of the current route as a positive integer, else null. */
export function useProjectIdParam(): number | null {
  return parseId(useParams().projectId);
}

/** `:paperId` of the current route as a positive integer, else null. */
export function usePaperIdParam(): number | null {
  return parseId(useParams().paperId);
}

/** `?project=` (PaperPage context) as a positive integer, else null. */
export function useProjectSearchParam(): number | null {
  const [searchParams] = useSearchParams();
  return parseId(searchParams.get("project"));
}

/** Search param that opens the PaperDetailSheet (`?paper=<id>`). */
export const PAPER_SHEET_PARAM = "paper";
const SHEET_HISTORY_FLAG = "rrPaperSheet";

export interface PaperSheetParam {
  /** The open paper, or null when the sheet is closed. */
  paperId: number | null;
  /** Push `?paper=<id>` (a new history entry, so Back closes the sheet). */
  openPaper: (paperId: number) => void;
  /** Close: go back when the sheet was opened in-app, else drop the param (deep link). */
  closePaper: () => void;
}

/** `?paper=<id>` state for FeedPage, LibraryPage and GraphPage (UI.md §5). */
export function usePaperSheetParam(): PaperSheetParam {
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const paperId = parseId(searchParams.get(PAPER_SHEET_PARAM));
  const openedInApp =
    typeof location.state === "object" &&
    location.state !== null &&
    (location.state as Record<string, unknown>)[SHEET_HISTORY_FLAG] === true;

  const openPaper = useCallback(
    (id: number) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          next.set(PAPER_SHEET_PARAM, String(id));
          return next;
        },
        { state: { [SHEET_HISTORY_FLAG]: true } },
      );
    },
    [setSearchParams],
  );

  const closePaper = useCallback(() => {
    if (openedInApp) {
      void navigate(-1);
      return;
    }
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete(PAPER_SHEET_PARAM);
        return next;
      },
      { replace: true },
    );
  }, [navigate, openedInApp, setSearchParams]);

  return useMemo(() => ({ paperId, openPaper, closePaper }), [paperId, openPaper, closePaper]);
}
