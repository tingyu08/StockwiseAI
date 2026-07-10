"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiRequest } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

export interface Group {
  id: number;
  name: string;
}

export interface ReorderItem {
  symbol: string;
  group_id: number | null;
  sort_order: number;
}

export function useGroups() {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["groups", market],
    queryFn: () => apiGet<Group[]>("/groups", {}, market),
  });
}

export function useCreateGroup() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      apiRequest<Group>("/groups", {
        method: "POST", body: { market: market.toUpperCase(), name },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups", market] }),
  });
}

export function useRenameGroup() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      apiRequest<Group>(`/groups/${id}`, { method: "PATCH", body: { name } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups", market] }),
  });
}

export function useDeleteGroup() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiRequest(`/groups/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["groups", market] });
      qc.invalidateQueries({ queryKey: ["watchlist", market] });
    },
  });
}

export function useReorderWatchlist() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: ReorderItem[]) =>
      apiRequest("/watchlist/reorder", {
        method: "PUT", body: { market: market.toUpperCase(), items },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}

export function useSetGroup() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ symbol, groupId }: { symbol: string; groupId: number | null }) =>
      apiRequest(`/watchlist/${symbol}`, {
        method: "PATCH",
        market,
        body: groupId === null ? { clear_group: true } : { group_id: groupId },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}

export interface DailyBriefing {
  global_market: {
    index_comments: string[];
    key_stocks_comment: string;
    risk_sentiment: "risk_on" | "risk_neutral" | "risk_off";
    one_liner: string;
  };
  local_market: {
    support: number;
    resistance: number;
    levels_rationale: string;
    flow_comment: string;
    prediction: string;
    prediction_rationales: string[];
  };
  stock_notes: {
    symbol: string;
    yesterday: string;
    technical: string;
    action: "買進" | "持有" | "減碼" | "觀望";
    rationale: string;
    entry_price: number;
    stop_loss: number;
    target_price: number;
  }[];
  risks: {
    events: string[];
    black_swan_watch: string[];
    monitor_signals: string[];
  };
  overall_stance: "bullish" | "neutral" | "bearish";
}

export interface OverviewData {
  market: string;
  trade_date: string;
  model: string;
  report: DailyBriefing;
  created_at: string | null;
}

export function useOverview() {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["overview", market],
    queryFn: () => apiGet<OverviewData>("/analysis/overview", {}, market),
    retry: false,
  });
}

export function useRunOverview() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiRequest<OverviewData>("/analysis/overview:run", { method: "POST", market }),
    onSuccess: (data) => {
      qc.setQueryData(["overview", market], data);
      qc.invalidateQueries({ queryKey: ["usage"] });
      qc.invalidateQueries({ queryKey: ["analysis"] });
    },
  });
}
