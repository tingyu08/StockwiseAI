"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  apiGet,
  apiRequest,
  ApiError,
  removeActiveJob,
  trackActiveJob,
  waitForJob,
  type StartedJob,
} from "@/lib/api";
import type { NewsData } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

export type { NewsData } from "@/lib/types";

export function useNews(symbol: string) {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["news", market, symbol],
    queryFn: () => apiGet<NewsData>(`/stocks/${symbol}/news`, {}, market),
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 404) && count < 1,
  });
}

export function useRunNews(symbol: string) {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const started = await apiRequest<StartedJob>(`/stocks/${symbol}/news:run`, {
        method: "POST",
        market,
      });
      trackActiveJob({ runId: started.run_id, name: `${symbol} 新聞研究` });
      const result = await waitForJob<NewsData>(started.run_id);
      removeActiveJob(started.run_id);
      return result;
    },
    onSuccess: (data) => {
      qc.setQueryData(["news", market, symbol], data);
      qc.invalidateQueries({ queryKey: ["usage"] });
    },
  });
}
