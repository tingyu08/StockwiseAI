"use client";

import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import { useOverview, useRunOverview } from "@/hooks/use-groups";
import { ApiError } from "@/lib/api";

const STANCE = {
  bullish: { label: "偏多", cls: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200" },
  neutral: { label: "中性", cls: "bg-neutral-200 text-neutral-700 dark:bg-neutral-700 dark:text-neutral-200" },
  bearish: { label: "偏空", cls: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200" },
} as const;

export function OverviewCard() {
  const { data, isLoading, error } = useOverview();
  const run = useRunOverview();
  const noData = error instanceof ApiError && error.status === 404;

  return (
    <section className="rounded-xl border border-neutral-200 p-6 dark:border-neutral-800">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">🧠 AI 投資組合總評</h2>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
          title="對全部自選股跑當日分析（已分析的直接沿用），再產生一份組合總評"
        >
          {run.isPending ? "分析中（約 10~30 秒）…" : "一鍵分析全部自選＋總評"}
        </button>
      </div>

      {isLoading && <p className="text-sm text-neutral-500">載入中…</p>}
      {noData && !data && (
        <p className="text-sm text-neutral-500">
          尚無今日總評。點右上按鈕，AI 會分析所有自選股並給出整體評語。
        </p>
      )}
      {run.isError && <p className="text-sm text-red-500">{(run.error as Error).message}</p>}

      {data && (
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-3">
            <span className={`rounded-full px-3 py-1 font-semibold ${STANCE[data.report.overall_stance].cls}`}>
              整體{STANCE[data.report.overall_stance].label}
            </span>
            <span className="text-xs text-neutral-400">
              {data.trade_date}｜{data.model}
            </span>
          </div>
          <p><span className="font-medium text-neutral-500">市場觀察：</span>{data.report.market_comment}</p>
          <p><span className="font-medium text-neutral-500">組合評語：</span>{data.report.portfolio_comment}</p>
          {data.report.top_picks.length > 0 && (
            <div>
              <span className="font-medium text-neutral-500">最值得關注：</span>
              <ul className="mt-1 space-y-1">
                {data.report.top_picks.map((p) => (
                  <li key={p.symbol}>
                    <span className="font-mono font-semibold">{p.symbol}</span>
                    <span className="ml-2 text-neutral-600 dark:text-neutral-300">{p.comment}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.report.cautions.length > 0 && (
            <div>
              <span className="font-medium text-neutral-500">留意風險：</span>
              <ul className="mt-1 list-inside list-disc space-y-0.5 text-neutral-600 dark:text-neutral-300">
                {data.report.cautions.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-xs text-neutral-400">僅供參考，不構成投資建議。</p>
        </div>
      )}
      <FreshnessNote>{FRESHNESS.overview}</FreshnessNote>
    </section>
  );
}
