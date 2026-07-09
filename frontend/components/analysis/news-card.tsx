"use client";

import { FreshnessNote } from "@/components/freshness-note";
import { useNews, useRunNews } from "@/hooks/use-news";
import { useUsage } from "@/hooks/use-analysis";
import { ApiError } from "@/lib/api";

export function NewsCard({ symbol }: { symbol: string }) {
  const { data, isLoading, error } = useNews(symbol);
  const run = useRunNews(symbol);
  const { data: usage } = useUsage();

  const remaining =
    usage?.find((u) => u.model.startsWith("antigravity"))?.remaining ?? null;
  const noNews = error instanceof ApiError && error.status === 404;

  return (
    <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-lg font-semibold">📰 新聞面研究</h3>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending || remaining === 0}
          title={remaining === 0 ? "今日新聞研究額度已用盡，明日恢復" : ""}
          className="rounded-md border border-neutral-300 px-3 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {run.isPending ? "AI 搜尋新聞中（約 1~3 分鐘）…" : "搜尋新聞"}
        </button>
      </div>

      {isLoading && <p className="text-sm text-neutral-500">載入新聞研究中…</p>}
      {noNews && !data && !run.isPending && (
        <p className="text-sm text-neutral-500">
          尚無近期新聞研究。點「搜尋新聞」，AI 會自行上網搜尋近 7 天新聞並摘要
          （結果同時餵給當日 AI 分析）。
        </p>
      )}
      {run.isError && (
        <p className="mb-2 text-sm text-red-500">{(run.error as Error).message}</p>
      )}
      {data && (
        <div className="space-y-3">
          <p className="whitespace-pre-line text-sm leading-relaxed">{data.summary}</p>
          <p className="text-xs text-neutral-400">
            {data.date} 研究｜{data.model}
          </p>
        </div>
      )}

      <FreshnessNote>
        每日於例行 AI 批次分析前自動研究一次（AI 託管股）；摘要保鮮 4 天內會納入個股分析輸入。
      </FreshnessNote>
    </section>
  );
}
