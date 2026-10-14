import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MotionPreferenceProvider } from "../lib/motion";
import { ThemeProvider } from "../lib/theme";

/**
 * Data + preference providers shared by the app (main.tsx) and tests: TanStack Query, theme and
 * motion preference. main.tsx adds MotionConfig, the router and the Toaster inside these.
 */
export function AppProviders({ queryClient, children }: { queryClient: QueryClient; children?: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MotionPreferenceProvider>{children}</MotionPreferenceProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
