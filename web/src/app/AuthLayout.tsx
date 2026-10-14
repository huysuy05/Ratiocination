/**
 * Layout of the sign-in pages (UI.md §6): the brand lockup and a centred card over the radar backdrop,
 * no app shell.
 * In local mode (no accounts) every auth page redirects home. Pages read the auth config with
 * `useAuthLayoutContext()`.
 */
import { CloudOff } from "lucide-react";
import { motion } from "motion/react";
import { Link, Navigate, Outlet, useOutletContext } from "react-router";
import { useAuthConfig } from "../api/hooks";
import type { AuthConfig } from "../api/types";
import { BrandLockup } from "../components/ui/BrandMark";
import { Button } from "../components/ui/Button";
import { RadarBackdrop, RadarScope } from "../components/ui/RadarSweep";
import { SPRING_GENTLE } from "../lib/motion";
import { LICENSE_NAME, SOURCE_URL } from "../lib/source";
import { paths } from "./paths";
import { AppLoading } from "./RouteFallbacks";

export interface AuthLayoutContext {
  config: AuthConfig;
}

export function useAuthLayoutContext(): AuthLayoutContext {
  return useOutletContext<AuthLayoutContext>();
}

export function AuthLayout() {
  const config = useAuthConfig();
  if (config.isPending) return <AppLoading />;
  if (config.data?.mode === "local") return <Navigate to={paths.root()} replace />;

  return (
    <div className="relative isolate flex min-h-dvh flex-col items-center overflow-hidden bg-bg px-4 py-10 text-text">
      <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[640px] app-backdrop" />
      <RadarBackdrop className="-z-10 opacity-70" ringGap={88} />
      <div aria-hidden="true" className="pointer-events-none absolute top-[12vh] left-1/2 -z-10 -translate-x-1/2 opacity-[0.18] blur-[1px]">
        <RadarScope size="lg" className="scale-[2.6]" />
      </div>
      <Link to={paths.root()} aria-label="Ratiocination home" className="mb-8 flex items-center rounded-control p-1.5 text-text">
        <BrandLockup height={30} />
      </Link>
      <motion.main
        id="main"
        initial={{ opacity: 0, y: 12, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={SPRING_GENTLE}
        className="w-full max-w-[420px] rounded-card border border-border bg-surface/90 p-6 backdrop-blur-xl surface-sheen elevation-raised sm:p-8"
      >
        {config.data ? (
          <Outlet context={{ config: config.data } satisfies AuthLayoutContext} />
        ) : (
          <div role="alert" className="flex flex-col items-center gap-4 text-center">
            <CloudOff aria-hidden="true" className="size-8 text-not-relevant" />
            <p className="text-muted">{config.error?.message ?? "The server did not answer."}</p>
            <Button variant="primary" onClick={() => void config.refetch()} loading={config.isFetching}>
              Try again
            </Button>
          </div>
        )}
      </motion.main>
      <p className="mt-8 max-w-[420px] text-center text-[12.5px] text-faint">
        Your projects, ratings and documents are private to your account.{" "}
        <a href={SOURCE_URL} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-muted">
          Source code ({LICENSE_NAME})
        </a>
      </p>
    </div>
  );
}
