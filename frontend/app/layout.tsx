import type { Metadata } from "next";
import { Providers } from "@/components/providers";
import "@fontsource-variable/manrope";
import "./globals.css";

export const metadata: Metadata = {
  title: "研辩台 · 论文工作台",
  description: "可追溯的论文评审与格式修订工作台",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
