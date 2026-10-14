/**
 * Auth gate for the app routes (UI.md §4). Reads `GET /api/me`: signed out (401) → `/signin?next=`;
 * the client's `radar:unauthorized` event (a session that expired mid-use) re-checks `me`, which then
 * redirects. In local mode `me` always succeeds.
 */
import { useQueryClient } from "@tanstack/react-query";
import { CloudOff } from "lucide-react";
import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { UNAUTHORIZED_EVENT, type UnauthorizedEventDetail } from "../api/client";
import { qk, useMe } from "../api/hooks";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { paths } from "./paths";
import { AppLoading } from "./RouteFallbacks";

const ME_PATH = "/api/me";

export function RequireAuth() {
  const me = useMe();
  const location = useLocation();
  const queryClient = useQueryClient();

  useEffect(() => {
    const onUnauthorized = (event: Event) => {
      // A 401 from /api/me itself already reached this gate through the query; re-checking it
      // would answer 401 again and loop forever.
      if ((event as CustomEvent<UnauthorizedEventDetail>).detail?.path === ME_PATH) return;
      void queryClient.invalidateQueries({ queryKey: qk.me() });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [queryClient]);

  if (me.error?.status === 401) {
    return <Navigate to={paths.signIn(`${location.pathname}${location.search}`)} replace />;
  }
  if (me.data) return <Outlet />;
  if (me.isPending) return <AppLoading />;
  return (
    <div className="grid min-h-dvh place-items-center bg-bg px-4">
      <EmptyState
        icon={<CloudOff aria-hidden="true" />}
        title="Ratiocination is not reachable"
        description={me.error?.message ?? "The server did not answer."}
        actions={
          <Button variant="primary" onClick={() => void me.refetch()} loading={me.isFetching}>
            Try again
          </Button>
        }
      />
    </div>
  );
}
