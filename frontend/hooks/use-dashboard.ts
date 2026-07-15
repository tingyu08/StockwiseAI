"use client";

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { DASHBOARD_STALE_MS } from "@/lib/query-policy";
import type { StockDashboard } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

export { DASHBOARD_STALE_MS } from "@/lib/query-policy";

export function useStockDashboard(symbol: string, range: string) {
  const market = useMarketStore((state) => state.market);
  return useQuery({
    queryKey: ["stock-dashboard", market, symbol, range],
    queryFn: () =>
      apiGet<StockDashboard>(
        `/stocks/${symbol}/dashboard`,
        { range },
        market,
      ),
    enabled: Boolean(symbol),
    staleTime: DASHBOARD_STALE_MS,
  });
}
