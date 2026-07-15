"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiRequest, trackActiveJob } from "@/lib/api";
import { PRICE_STALE_MS, WATCHLIST_STALE_MS } from "@/lib/query-policy";
import type { PriceSeries, StockInfo, WatchItem } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

interface AddWatchResult {
  symbol: string;
  market: string;
  name: string;
  started: boolean;
  job: string | null;
  run_id: number | null;
}

export function usePrices(symbol: string, range: string) {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["prices", market, symbol, range],
    queryFn: () =>
      apiGet<PriceSeries>(`/stocks/${symbol}/prices`, { range }, market),
    enabled: !!symbol,
    staleTime: PRICE_STALE_MS,
  });
}

export function useSearch(q: string) {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["search", market, q],
    queryFn: () => apiGet<StockInfo[]>("/stocks", { q }, market),
    enabled: q.trim().length > 0,
  });
}

export function useWatchlist() {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["watchlist", market],
    queryFn: () => apiGet<WatchItem[]>("/watchlist", {}, market),
    staleTime: WATCHLIST_STALE_MS,
  });
}

export function useAddWatch() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) => apiRequest<AddWatchResult>("/watchlist", {
      method: "POST", body: { market: market.toUpperCase(), symbol },
    }),
    onSuccess: (result) => {
      if (result.run_id !== null && result.job) {
        trackActiveJob({ runId: result.run_id, name: result.job });
      }
      qc.invalidateQueries({ queryKey: ["watchlist", market] });
    },
  });
}

export function useRemoveWatch() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) => apiRequest(`/watchlist/${symbol}`, {
      method: "DELETE", market,
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}
