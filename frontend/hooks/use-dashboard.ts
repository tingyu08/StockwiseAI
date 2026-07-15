"use client";

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { StockDashboard } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

export const DASHBOARD_STALE_MS = 5 * 60_000;

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
