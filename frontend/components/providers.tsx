"use client";

import { ThemeProvider } from "next-themes";
import { TooltipProvider } from "@/components/ui/tooltip";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="light"
      forcedTheme="light"
      enableSystem={false}
      storageKey="thesisdebate-replica-light-v2"
      disableTransitionOnChange
    >
      <TooltipProvider delay={350}>{children}</TooltipProvider>
    </ThemeProvider>
  );
}
