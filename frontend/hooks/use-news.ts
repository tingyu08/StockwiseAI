"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, ApiError } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8123";

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
    mutationFn: async () => {
      const res = await fetch(
        `${API_BASE}/api/v1/stocks/${symbol}/news:run?market=${market.toUpperCase()}`,
        { method: "POST" },
      );
      const body = await res.json();
      if (!body.success) throw new Error(body.error ?? "新聞研究失敗");
      return body.data as NewsData;
    },
    onSuccess: (data) => {
      qc.setQueryData(["news", market, symbol], data);
      qc.invalidateQueries({ queryKey: ["usage"] });
    },
  });
}
