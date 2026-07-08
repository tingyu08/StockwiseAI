"use client";

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

export interface PremiumRow {
  symbol: string;
  name: string;
  date: string | null;
  nav: number | null;
  close: number | null;
  premium_pct: number | null;
}

export interface PremiumHistoryPoint {
  date: string;
  nav: number | null;
  close: number | null;
  premium_pct: number | null;
}

export interface PredictionBandPoint {
  date: string;
  mid: number;
  upper: number;
  lower: number;
}

export interface PredictionData {
  trade_date: string;
  method: string;
  horizons: Record<string, PredictionBandPoint[]>;
  disclaimer: string;
}

export function usePremiumList() {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["premium", market],
    queryFn: () => apiGet<PremiumRow[]>("/premium", {}, market),
  });
}

export function usePremiumHistory(symbol: string | null) {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["premium-history", market, symbol],
    queryFn: () => apiGet<PremiumHistoryPoint[]>(`/premium/${symbol}/history`, {}, market),
    enabled: !!symbol,
  });
}

export function usePredictions(symbol: string) {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["predictions", market, symbol],
    queryFn: () => apiGet<PredictionData>(`/stocks/${symbol}/predictions`, {}, market),
    enabled: !!symbol,
    retry: 1,
  });
}
