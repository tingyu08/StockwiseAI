"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiRequest, ApiError } from "@/lib/api";
import type { AnalysisData, UsageRow } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

export type { AnalysisData, Scenario, UsageRow } from "@/lib/types";

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
    mutationFn: () => apiRequest<AnalysisData>(
      `/stocks/${symbol}/analysis:${kind}`,
      { method: "POST", market },
    ),
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
