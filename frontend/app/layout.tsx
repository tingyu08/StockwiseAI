import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import "./globals.css";

import { MarketSwitch } from "@/components/market-switch";
import { ApiTokenControl } from "@/components/api-token-control";
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
              <nav className="flex gap-4 text-sm text-neutral-500">
                <Link href="/" className="hover:text-neutral-900 dark:hover:text-white">儀表板</Link>
                <Link href="/compare" className="hover:text-neutral-900 dark:hover:text-white">比較</Link>
                <Link href="/premium" className="hover:text-neutral-900 dark:hover:text-white">折溢價</Link>
                <Link href="/simulation" className="hover:text-neutral-900 dark:hover:text-white">模擬交易</Link>
                <Link href="/backtest" className="hover:text-neutral-900 dark:hover:text-white">回測</Link>
              </nav>
            </div>
            <div className="flex items-center gap-2">
              <ApiTokenControl />
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
