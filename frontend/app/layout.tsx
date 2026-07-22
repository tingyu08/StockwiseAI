import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import "./globals.css";

import { MainNav } from "@/components/main-nav";
import { MarketSwitch } from "@/components/market-switch";
import { JobCenter } from "@/components/job-center";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: "Stock AI Advisor",
  description: "AI 股票分析與模擬交易平台（台股＋美股）",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-Hant">
      <body className="min-h-screen bg-white text-neutral-900 antialiased dark:bg-neutral-950 dark:text-neutral-100">
        <Providers>
        <header className="sticky top-0 z-10 border-b border-neutral-200 bg-white/80 backdrop-blur dark:border-neutral-800 dark:bg-neutral-950/80">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
            <div className="flex items-center gap-6">
              <h1 className="text-lg font-semibold">
                <Link href="/">📈 Stock AI Advisor</Link>
              </h1>
              <MainNav />
            </div>
            <div className="flex items-center gap-2">
              <JobCenter />
              <Suspense>
                <MarketSwitch />
              </Suspense>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
        <footer className="mx-auto max-w-6xl px-4 py-8 text-center text-xs text-neutral-400">
          所有 AI 分析與預測僅供參考，不構成投資建議。
        </footer>
        </Providers>
      </body>
    </html>
  );
}
