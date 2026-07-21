"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiRequest, ApiError } from "@/lib/api";
import { STORED_REPORT_STALE_MS, USAGE_STALE_MS } from "@/lib/query-policy";
import type { AnalysisData, StockDashboard, UsageRow } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

export type { AnalysisData, Scenario, UsageRow } from "@/lib/types";

export function useAnalysis(symbol: string) {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["analysis", market, symbol],
    queryFn: () => apiGet<AnalysisData>(`/stocks/${symbol}/analysis`, {}, market),
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 404) && count < 1,
    staleTime: STORED_REPORT_STALE_MS,
  });
}

export function useUsage() {
  return useQuery({
    queryKey: ["usage"],
    queryFn: () => apiGet<UsageRow[]>("/usage"),
    refetchInterval: 60_000,
    staleTime: USAGE_STALE_MS,
  });
}

function useRunAnalysis(kind: "routine" | "deep", symbol: string) {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiRequest<AnalysisData>(
      `/stocks/${symbol}/analysis:${kind}`,
      // AI 推理開啟後單次分析可達 30 秒以上（後端曾實測 30.9s），
      // 預設 30s timeout 會在後端即將成功時放棄 → 給 3 分鐘
      { method: "POST", market, timeoutMs: 180_000 },
    ),
    onSuccess: (data) => {
      qc.setQueryData(["analysis", market, symbol], data);
      qc.setQueriesData<StockDashboard>(
        { queryKey: ["stock-dashboard", market, symbol] },
        (current) => current ? { ...current, analysis: data } : current,
      );
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
