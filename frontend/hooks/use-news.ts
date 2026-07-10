"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiRequest, ApiError } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

export interface NewsData {
  date: string;
  model: string;
  summary: string;
  created_at: string | null;
}

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
    mutationFn: () => apiRequest<NewsData>(
      `/stocks/${symbol}/news:run`,
      { method: "POST", market },
    ),
    onSuccess: (data) => {
      qc.setQueryData(["news", market, symbol], data);
      qc.invalidateQueries({ queryKey: ["usage"] });
    },
  });
}
