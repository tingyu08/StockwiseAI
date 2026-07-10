"use client";

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

interface MarketFreshness {
  latest_price_date: string | null;
  latest_nav_date: string | null;
  latest_ai_date: string | null;
  latest_ai_dates?: {
    news: string | null;
    routine: string | null;
    trade: string | null;
  };
  latest_successful_job?: {
    id: number;
    name: string;
    finished_at: string | null;
  } | null;
}

export function DataStatus() {
  const market = useMarketStore((state) => state.market).toUpperCase() as "TW" | "US";
  const { data } = useQuery({
    queryKey: ["data-status"],
    queryFn: () => apiGet<Record<"TW" | "US", MarketFreshness>>("/data-status"),
    refetchInterval: 5 * 60_000,
  });
  const status = data?.[market];
  if (!status) return null;

  return (
    <div className="flex flex-wrap gap-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-500 dark:bg-neutral-900">
      <span>資料狀態</span>
      <span>行情 {status.latest_price_date ?? "尚無"}</span>
      <span>NAV {status.latest_nav_date ?? "尚無"}</span>
      {status.latest_ai_dates ? (
        <>
          <span>新聞 {status.latest_ai_dates.news ?? "尚無"}</span>
          <span>例行 {status.latest_ai_dates.routine ?? "尚無"}</span>
          <span>交易 {status.latest_ai_dates.trade ?? "尚無"}</span>
        </>
      ) : (
        <span>AI {status.latest_ai_date ?? "尚無"}</span>
      )}
      {status.latest_successful_job && (
        <span title={status.latest_successful_job.finished_at ?? undefined}>
          最近工作 {status.latest_successful_job.name}
        </span>
      )}
    </div>
  );
}
