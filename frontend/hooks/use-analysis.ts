"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, ApiError } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8123";

export interface Scenario {
  target_price: number;
  trigger_condition: string;
  probability: number;
}

export interface AnalysisData {
  trade_date: string;
  kind: "routine" | "deep" | "news";
  model: string;
  report: {
    symbol: string;
    action: "buy" | "sell" | "hold";
    confidence: number;
    target_price_low: number;
    target_price_high: number;
    stop_loss: number;
    reasoning: string;
    scenarios: { bull: Scenario; base: Scenario; bear: Scenario };
    risks: string[];
  };
  created_at: string | null;
}

export interface UsageRow {
  model: string;
  rpd: number;
  used: number;
  remaining: number;
}

export function useAnalysis(symbol: string) {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["analysis", market, symbol],
    queryFn: () => apiGet<AnalysisData>(`/stocks/${symbol}/analysis`, {}, market),
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 404) && count < 1,
  });
}

export function useUsage() {
  return useQuery({
    queryKey: ["usage"],
    queryFn: () => apiGet<UsageRow[]>("/usage"),
    refetchInterval: 60_000,
  });
}

function useRunAnalysis(kind: "routine" | "deep", symbol: string) {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await fetch(
        `${API_BASE}/api/v1/stocks/${symbol}/analysis:${kind}?market=${market.toUpperCase()}`,
        { method: "POST" },
      );
      const body = await res.json();
      if (!body.success) throw new Error(body.error ?? "分析失敗");
      return body.data as AnalysisData;
    },
    onSuccess: (data) => {
      qc.setQueryData(["analysis", market, symbol], data);
      qc.invalidateQueries({ queryKey: ["usage"] });
    },
  });
}

export function useRunRoutine(symbol: string) {
  return useRunAnalysis("routine", symbol);
}

export function useRunDeep(symbol: string) {
  return useRunAnalysis("deep", symbol);
}
