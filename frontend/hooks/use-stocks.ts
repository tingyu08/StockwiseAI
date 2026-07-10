"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiRequest } from "@/lib/api";
import type { PriceSeries, StockInfo, WatchItem } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

export function usePrices(symbol: string, range: string) {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["prices", market, symbol, range],
    queryFn: () =>
      apiGet<PriceSeries>(`/stocks/${symbol}/prices`, { range }, market),
    enabled: !!symbol,
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
  });
}

export function useAddWatch() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) => apiRequest("/watchlist", {
      method: "POST", body: { market: market.toUpperCase(), symbol },
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
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
