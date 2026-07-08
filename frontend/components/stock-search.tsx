"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAddWatch, useSearch } from "@/hooks/use-stocks";
import { useMarketStore } from "@/stores/market";

export function StockSearch() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const market = useMarketStore((s) => s.market);
  const { data: results, isFetching } = useSearch(submitted);
  const addWatch = useAddWatch();
  const router = useRouter();

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(q.trim());
        }}
        className="flex gap-2"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={market === "tw" ? "輸入代號或名稱，如 2330、台積電" : "輸入代號，如 AAPL"}
          className="flex-1 rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
        />
        <button
          type="submit"
          disabled={!q.trim() || isFetching}
          className="rounded-lg bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
        >
          {isFetching ? "搜尋中…" : "搜尋"}
        </button>
      </form>

      {results && results.length === 0 && (
        <p className="text-sm text-neutral-500">查無「{submitted}」，請確認代號或名稱。</p>
      )}
      {results && results.length > 0 && (
        <ul className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
          {results.map((r) => (
            <li key={r.symbol} className="flex items-center justify-between px-4 py-2">
              <button
                onClick={() => router.push(`/stock/${r.symbol}?market=${market}`)}
                className="text-left text-sm hover:underline"
              >
                <span className="font-mono font-semibold">{r.symbol}</span>
                <span className="ml-2 text-neutral-500">{r.name}</span>
                {r.kind === "etf" && (
                  <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700 dark:bg-blue-900 dark:text-blue-200">
                    ETF
                  </span>
                )}
              </button>
              <button
                onClick={() => addWatch.mutate(r.symbol)}
                disabled={addWatch.isPending}
                className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
              >
                {addWatch.isPending ? "同步中…" : "＋自選"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {addWatch.isError && (
        <p className="text-sm text-red-500">{(addWatch.error as Error).message}</p>
      )}
    </div>
  );
}
