"use client";

import { AlertsPanel } from "@/components/alerts-panel";
import { DataStatus } from "@/components/data-status";
import { OverviewCard } from "@/components/overview-card";
import { StockSearch } from "@/components/stock-search";
import { WatchlistPanel } from "@/components/watchlist-panel";
import { MARKET_LABELS, useMarketStore } from "@/stores/market";

export default function Dashboard() {
  const market = useMarketStore((s) => s.market);

  return (
    <div className="space-y-6">
      <DataStatus />
      <section className="rounded-xl border border-neutral-200 p-6 dark:border-neutral-800">
        <h2 className="mb-3 text-lg font-semibold">搜尋 {MARKET_LABELS[market]}</h2>
        <StockSearch />
      </section>

      <OverviewCard />

      <section className="rounded-xl border border-neutral-200 p-6 dark:border-neutral-800">
        <h2 className="mb-3 text-lg font-semibold">自選清單</h2>
        <WatchlistPanel />
      </section>

      <section className="rounded-xl border border-neutral-200 p-6 dark:border-neutral-800">
        <h2 className="mb-3 text-lg font-semibold">🔔 警示</h2>
        <AlertsPanel />
      </section>
    </div>
  );
}
