"use client";

import { FreshnessNote } from "@/components/freshness-note";
import { useRunNews } from "@/hooks/use-news";
import type { NewsData } from "@/lib/types";

interface NewsCardProps {
  symbol: string;
  data: NewsData | null;
  isLoading: boolean;
}

export function NewsCard({ symbol, data, isLoading }: NewsCardProps) {
  const run = useRunNews(symbol);
  const noNews = data === null;

  return (
    <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-lg font-semibold">📰 新聞面研究</h3>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="rounded-md border border-neutral-300 px-3 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {run.isPending ? "抓新聞並摘要中（約 10~30 秒）…" : "研究新聞"}
        </button>
      </div>

      {isLoading && <p className="text-sm text-neutral-500">載入新聞研究中…</p>}
      {noNews && !data && !run.isPending && (
        <p className="text-sm text-neutral-500">
          尚無近期新聞研究。點「研究新聞」，系統會抓近 7 天的新聞標題，
          再交給 AI 摘要（出處為系統提供，結果同時餵給當日 AI 分析）。
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
