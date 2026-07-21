"use client";

import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import { useRunRoutine } from "@/hooks/use-analysis";
import type { AnalysisData } from "@/lib/types";

const ACTION_STYLE: Record<string, { label: string; cls: string }> = {
  buy: { label: "買進", cls: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200" },
  sell: { label: "賣出", cls: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200" },
  hold: { label: "觀望", cls: "bg-neutral-200 text-neutral-700 dark:bg-neutral-700 dark:text-neutral-200" },
};

const SCENARIO_LABEL = { bull: "樂觀", base: "中性", bear: "悲觀" } as const;

interface ReportCardProps {
  symbol: string;
  data: AnalysisData | null;
  isLoading: boolean;
}

export function ReportCard({ symbol, data, isLoading }: ReportCardProps) {
  const routine = useRunRoutine(symbol);

  const noReport = data === null;

  return (
    <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-lg font-semibold">🤖 AI 分析</h3>
        <button
          onClick={() => routine.mutate()}
          disabled={routine.isPending}
          className="rounded-md border border-neutral-300 px-3 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {routine.isPending ? "分析中…" : "產生分析"}
        </button>
      </div>

      {isLoading && <p className="text-sm text-neutral-500">載入分析中…</p>}
      {noReport && !data && (
        <p className="text-sm text-neutral-500">
          尚無當日分析報告，點右上「產生分析」（免費額度、約 10 秒）。
        </p>
      )}
      {routine.isError && (
        <p className="mb-2 text-sm text-red-500">
          {(routine.error as Error).message}
        </p>
      )}
      {data && <ReportBody data={data} />}

      <p className="mt-4 text-xs text-neutral-400">僅供參考，不構成投資建議。</p>
      <FreshnessNote>{FRESHNESS.analysis}</FreshnessNote>
    </section>
  );
}

function ReportBody({ data }: { data: AnalysisData }) {
  const r = data.report;
  const action = ACTION_STYLE[r.action];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`rounded-full px-3 py-1 text-sm font-semibold ${action.cls}`}>
          {action.label}
        </span>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-neutral-500">信心</span>
          <div className="h-2 w-24 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-700">
            <div
              className="h-full rounded-full bg-blue-500"
              style={{ width: `${r.confidence * 100}%` }}
            />
          </div>
          <span>{(r.confidence * 100).toFixed(0)}%</span>
        </div>
        <span className="text-sm text-neutral-500">
          目標 {r.target_price_low}–{r.target_price_high}｜停損 {r.stop_loss}
        </span>
      </div>

      <p className="text-sm leading-relaxed">{r.reasoning}</p>

      <div className="grid gap-2 sm:grid-cols-3">
        {(["bull", "base", "bear"] as const).map((key) => {
          const s = r.scenarios[key];
          return (
            <div
              key={key}
              className="rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800"
            >
              <div className="mb-1 flex justify-between">
                <span className="font-medium">{SCENARIO_LABEL[key]}</span>
                <span className="text-neutral-500">{(s.probability * 100).toFixed(0)}%</span>
              </div>
              <div className="text-lg font-semibold">{s.target_price}</div>
              <div className="mt-1 text-xs text-neutral-500">{s.trigger_condition}</div>
            </div>
          );
        })}
      </div>

      <div>
        <h4 className="mb-1 text-sm font-medium text-neutral-500">風險</h4>
        <ul className="list-inside list-disc space-y-0.5 text-sm">
          {r.risks.map((risk) => (
            <li key={risk}>{risk}</li>
          ))}
        </ul>
      </div>

      <p className="text-xs text-neutral-400">
        {data.trade_date}｜{data.kind === "deep" ? "深度分析" : "例行分析"}｜{data.model}
      </p>
    </div>
  );
}
