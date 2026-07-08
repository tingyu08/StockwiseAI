"use client";

import Link from "next/link";

import { useRemoveWatch, useWatchlist } from "@/hooks/use-stocks";
import { useMarketStore } from "@/stores/market";

export function WatchlistPanel() {
  const market = useMarketStore((s) => s.market);
  const { data: items, isLoading, isError, error } = useWatchlist();
  const removeWatch = useRemoveWatch();

  if (isLoading) return <p className="text-sm text-neutral-500">載入自選清單中…</p>;
  if (isError)
    return <p className="text-sm text-red-500">{(error as Error).message}</p>;
  if (!items || items.length === 0)
    return <p className="text-sm text-neutral-500">尚無自選股，請由上方搜尋加入。</p>;

  return (
    <ul className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
      {items.map((w) => (
        <li key={w.symbol} className="flex items-center justify-between px-4 py-2">
          <Link
            href={`/stock/${w.symbol}?market=${market}`}
            className="text-sm hover:underline"
          >
            <span className="font-mono font-semibold">{w.symbol}</span>
            <span className="ml-2 text-neutral-500">{w.name}</span>
          </Link>
          <button
            onClick={() => removeWatch.mutate(w.symbol)}
            disabled={removeWatch.isPending}
            className="text-xs text-neutral-400 hover:text-red-500 disabled:opacity-40"
          >
            移除
          </button>
        </li>
      ))}
    </ul>
  );
}
