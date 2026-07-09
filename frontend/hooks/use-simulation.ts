"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8123";

export interface Position {
  symbol: string;
  name: string;
  qty: number;
  avg_cost: number | null;
  close: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
}

export interface AccountView {
  market: string;
  currency: string;
  initial_cash: number;
  cash: number;
  equity: number;
  total_pnl: number;
  total_pnl_pct: number;
  positions: Position[];
  equity_curve: { date: string; equity: number }[];
}

export interface SimOrderView {
  id: number;
  symbol: string;
  name: string;
  side: "buy" | "sell";
  qty: number;
  fill_price: number | null;
  fee: number | null;
  status: "pending" | "filled" | "rejected";
  decided_by: string;
  reject_reason: string | null;
  created_at: string | null;
  filled_at: string | null;
  ai_report: {
    action: string;
    confidence: number;
    reasoning: string;
    stop_loss: number;
  } | null;
}

export function useSimAccount() {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["sim-account", market],
    queryFn: () => apiGet<AccountView>(`/simulation/${market.toUpperCase()}/account`),
  });
}

export function useSimOrders() {
  const market = useMarketStore((s) => s.market);
  return useQuery({
    queryKey: ["sim-orders", market],
    queryFn: () => apiGet<SimOrderView[]>(`/simulation/${market.toUpperCase()}/orders`),
  });
}

export function useToggleAiManaged() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ symbol, ai_managed }: { symbol: string; ai_managed: boolean }) => {
      const res = await fetch(
        `${API_BASE}/api/v1/watchlist/${symbol}?market=${market.toUpperCase()}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ai_managed }),
        },
      );
      const body = await res.json();
      if (!body.success) throw new Error(body.error ?? "設定失敗");
      return body.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}

export function useRunSimStep() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (step: "decide" | "fill") => {
      const res = await fetch(
        `${API_BASE}/api/v1/simulation/${market.toUpperCase()}:${step}`,
        { method: "POST" },
      );
      const body = await res.json();
      if (!body.success) throw new Error(body.error ?? "執行失敗");
      return body.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sim-account", market] });
      qc.invalidateQueries({ queryKey: ["sim-orders", market] });
    },
  });
}
