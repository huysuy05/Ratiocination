/**
 * Entry point: fonts + global styles, providers (QueryClient, theme, motion preference),
 * MotionConfig, the data router and the Toaster.
 */
import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import "./styles/globals.css";

import type { QueryClient } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router/dom";
import { Toaster } from "sonner";
import { AppProviders } from "./app/providers";
import { createQueryClient } from "./app/queryClient";
import { createAppRouter } from "./app/router";
import { useMotionPreference } from "./lib/motion";
import { useTheme } from "./lib/theme";

type AppRouter = ReturnType<typeof createAppRouter>;

function Root({ router }: { router: AppRouter }) {
  const { reduced } = useMotionPreference();
  const { resolved } = useTheme();
  return (
    <MotionConfig reducedMotion={reduced ? "always" : "never"}>
      <RouterProvider router={router} />
      <Toaster
        theme={resolved}
        position="bottom-center"
        closeButton
        duration={4000}
        mobileOffset={{ bottom: 76 }}
        toastOptions={{ className: "rr-toast" }}
      />
    </MotionConfig>
  );
}

function render(queryClient: QueryClient, router: AppRouter): void {
  const container = document.getElementById("root");
  if (!container) throw new Error("Missing #root element");
  createRoot(container).render(
    <StrictMode>
      <AppProviders queryClient={queryClient}>
        <Root router={router} />
      </AppProviders>
    </StrictMode>,
  );
}

const queryClient = createQueryClient();
render(queryClient, createAppRouter(queryClient));
