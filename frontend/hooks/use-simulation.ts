"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  apiGet,
  apiRequest,
  removeActiveJob,
  trackActiveJob,
  waitForJob,
  type StartedJob,
} from "@/lib/api";
import { useMarketStore } from "@/stores/market";

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

export interface SimStepResult {
  orders_created?: number;
  orders?: { symbol: string; side: string; qty?: number; reason?: string }[];
  skipped?: { symbol: string; reason: string }[];
  filled?: number;
  rejected?: number;
  waiting?: number;
  managed?: number;
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
    mutationFn: ({ symbol, ai_managed }: { symbol: string; ai_managed: boolean }) =>
      apiRequest(`/watchlist/${symbol}`, {
        method: "PATCH", market, body: { ai_managed },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", market] }),
  });
}

export function useRunSimStep() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (step: "decide" | "fill") => {
      if (step === "fill") {
        return apiRequest<SimStepResult>(`/simulation/${market.toUpperCase()}:fill`, {
          method: "POST",
        });
      }
      const started = await apiRequest<StartedJob>(
        `/simulation/${market.toUpperCase()}:decide`,
        { method: "POST" },
      );
      trackActiveJob({ runId: started.run_id, name: `${market.toUpperCase()} 模擬決策` });
      const result = await waitForJob<SimStepResult>(started.run_id);
      removeActiveJob(started.run_id);
      return result;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sim-account", market] });
      qc.invalidateQueries({ queryKey: ["sim-orders", market] });
    },
  });
}
