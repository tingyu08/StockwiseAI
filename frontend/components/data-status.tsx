"use client";

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { formatRunTime, formatRunTimeFull } from "@/lib/datetime";
import { useMarketStore } from "@/stores/market";

interface MarketFreshness {
  latest_price_date: string | null;
  latest_nav_date: string | null;
  latest_ai_runs?: {
    news: string | null;
    routine: string | null;
    trade: string | null;
  };
  latest_overview_run?: string | null;
  latest_successful_job?: {
    id: number;
    name: string;
    finished_at: string | null;
  } | null;
}

/** 排程執行時間；沒跑過就顯示「尚未執行」。 */
function RunAt({ label, at }: { label: string; at: string | null | undefined }) {
  const shown = `${label} ${formatRunTime(at) ?? "尚未執行"}`;
  // title 會取代無障礙名稱——沒有 aria-label 的話，螢幕閱讀器念到的是
  // 完整時間戳而不是「例行 07/09 17:38」。title 只該是滑鼠提示。
  return (
    <span title={formatRunTimeFull(at)} aria-label={shown}>
      {shown}
    </span>
  );
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
      {/* 行情/NAV 是「資料到哪一天」，與下方的「排程幾點跑的」語意不同，
          標籤明確寫出來避免再次混淆 */}
      <span>資料日期</span>
      <span>行情 {status.latest_price_date ?? "尚無"}</span>
      {status.latest_nav_date && <span>NAV {status.latest_nav_date}</span>}
      <span className="text-neutral-400 dark:text-neutral-600">｜</span>
      <span>排程執行</span>
      <RunAt label="新聞" at={status.latest_ai_runs?.news} />
      <RunAt label="例行" at={status.latest_ai_runs?.routine} />
      <RunAt label="交易" at={status.latest_ai_runs?.trade} />
      <RunAt label="簡報" at={status.latest_overview_run} />
      {status.latest_successful_job && (
        <span
          title={formatRunTimeFull(status.latest_successful_job.finished_at)}
          aria-label={`最近工作 ${status.latest_successful_job.name}`}
        >
          最近工作 {status.latest_successful_job.name}
        </span>
      )}
    </div>
  );
}
