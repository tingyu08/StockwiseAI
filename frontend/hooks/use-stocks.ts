"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { PriceSeries, StockInfo, WatchItem } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8123";

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
    mutationFn: async (symbol: string) => {
      const res = await fetch(`${API_BASE}/api/v1/watchlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market: market.toUpperCase(), symbol }),
      });
      const body = await res.json();
      if (!body.success) throw new Error(body.error ?? "加入失敗");
      return body.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}

export function useRemoveWatch() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string) => {
      const res = await fetch(
        `${API_BASE}/api/v1/watchlist/${symbol}?market=${market.toUpperCase()}`,
        { method: "DELETE" },
      );
      const body = await res.json();
      if (!body.success) throw new Error(body.error ?? "移除失敗");
      return body.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}
