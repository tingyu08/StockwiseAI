"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8123";

export interface Group {
  id: number;
  name: string;
}

export interface ReorderItem {
  symbol: string;
  group_id: number | null;
  sort_order: number;
}

async function post(path: string, method: string, body?: unknown) {
  const res = await fetch(`${API_BASE}/api/v1${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.error ?? "操作失敗");
  return json.data;
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
      post("/groups", "POST", { market: market.toUpperCase(), name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups", market] }),
  });
}

export function useDeleteGroup() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => post(`/groups/${id}`, "DELETE"),
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
      post("/watchlist/reorder", "PUT", { market: market.toUpperCase(), items }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}

export function useSetGroup() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ symbol, groupId }: { symbol: string; groupId: number | null }) =>
      post(
        `/watchlist/${symbol}?market=${market.toUpperCase()}`,
        "PATCH",
        groupId === null ? { clear_group: true } : { group_id: groupId },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}

export interface OverviewData {
  market: string;
  trade_date: string;
  model: string;
  report: {
    overall_stance: "bullish" | "neutral" | "bearish";
    market_comment: string;
    portfolio_comment: string;
    top_picks: { symbol: string; comment: string }[];
    cautions: string[];
  };
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
      post(`/analysis/overview:run?market=${market.toUpperCase()}`, "POST"),
    onSuccess: (data) => {
      qc.setQueryData(["overview", market], data);
      qc.invalidateQueries({ queryKey: ["usage"] });
      qc.invalidateQueries({ queryKey: ["analysis"] });
    },
  });
}
