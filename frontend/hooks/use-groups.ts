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

/** 名稱與昨收由系統直供（見 analysis_service.stock_facts），不取自 AI 的
 *  yesterday 字串——那要靠剖析中文才拿得到數值，且 AI 隨時可能改寫格式。
 *  舊簡報（本欄位上線前）不會有這個欄位，前端需自行退回顯示 yesterday。 */
export interface StockFact {
  name: string;
  close: number | null;
  /** 帶正負號；只有一天歷史時為 null（填 0 會被畫成平盤，等於謊報） */
  change_pct: number | null;
}

export interface OverviewData {
  market: string;
  trade_date: string;
  model: string;
  report: DailyBriefing;
  stock_facts?: Record<string, StockFact>;
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
    mutationFn: async () => {
      const started = await apiRequest<StartedJob>("/analysis/overview:run", {
        method: "POST",
        market,
      });
      trackActiveJob({ runId: started.run_id, name: `${market.toUpperCase()} 每日簡報` });
      const result = await waitForJob<OverviewData>(started.run_id);
      removeActiveJob(started.run_id);
      return result;
    },
    onSuccess: (data) => {
      qc.setQueryData(["overview", market], data);
      qc.invalidateQueries({ queryKey: ["analysis"] });
    },
  });
}
