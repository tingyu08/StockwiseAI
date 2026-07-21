"use client";

import { use, useState } from "react";

import { NewsCard } from "@/components/analysis/news-card";
import { ReportCard } from "@/components/analysis/report-card";
import { CandlestickChart } from "@/components/charts/candlestick";
import { TechnicalIndicatorsChart } from "@/components/charts/technical-indicators";
import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import { useStockDashboard } from "@/hooks/use-dashboard";
import { useMarketStore } from "@/stores/market";

const RANGES = [
  { key: "3m", label: "3 個月" },
  { key: "6m", label: "6 個月" },
  { key: "1y", label: "1 年" },
] as const;

export default function StockPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = use(params);
  const market = useMarketStore((s) => s.market);
  const [range, setRange] = useState<"3m" | "6m" | "1y">("1y");
  const [showPrediction, setShowPrediction] = useState(true);
  const { data, isLoading, isError, error } = useStockDashboard(symbol, range);

  const last = data?.series.at(-1);

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-2xl font-semibold">
            <span className="font-mono">{symbol}</span>
            {data && <span className="ml-3 text-lg text-neutral-500">{data.stock.name}</span>}
          </h2>
          {last?.close != null && (
            <p className="mt-1 text-sm text-neutral-500">
              {last.date} 收盤 {last.close.toLocaleString()} {data?.stock.currency}
              {last.rsi14 != null && `｜RSI ${last.rsi14.toFixed(1)}`}
              {last.kd_k != null && `｜K ${last.kd_k.toFixed(1)} D ${last.kd_d?.toFixed(1)}`}
            </p>
          )}
        </div>
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              className={`rounded-md px-3 py-1 text-sm ${
                range === r.key
                  ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                  : "text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <p className="text-sm text-neutral-500">載入走勢資料中…</p>}
      {isError && (
        <p className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {(error as Error).message}
        </p>
      )}
      {data && data.series.length > 0 && (
        <div className="rounded-xl border border-neutral-200 p-4 dark:border-neutral-800">
          <div className="mb-2 flex justify-end">
            <label className="flex items-center gap-1.5 text-xs text-neutral-500">
              <input
                type="checkbox"
                checked={showPrediction}
                onChange={(e) => setShowPrediction(e.target.checked)}
              />
              顯示 20 日回歸通道投影（僅供參考）
            </label>
          </div>
          <CandlestickChart
            data={data.series}
            market={market}
            prediction={showPrediction ? data.prediction?.horizons["20"] : undefined}
          />
          <TechnicalIndicatorsChart data={data.series} />
          <FreshnessNote>{FRESHNESS.prices}</FreshnessNote>
        </div>
      )}

      <ReportCard
        symbol={symbol}
        data={data?.analysis ?? null}
        isLoading={isLoading}
      />
      <NewsCard
        symbol={symbol}
        data={data?.news ?? null}
        usage={data?.usage ?? []}
        isLoading={isLoading}
      />
    </div>
  );
}
