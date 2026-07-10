"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useWatchlist } from "@/hooks/use-stocks";
import { apiGet, apiRequest } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

interface AlertRow {
  id: number;
  symbol: string;
  name: string;
  kind: string;
  kind_label: string;
  threshold: number;
  active: boolean;
  last_triggered: { date: string; value: number } | null;
}

const KIND_OPTS = [
  { key: "price_above", label: "價格高於" },
  { key: "price_below", label: "價格低於" },
  { key: "premium_above", label: "溢價高於(%)" },
  { key: "premium_below", label: "折價低於(%)" },
];

export function AlertsPanel() {
  const market = useMarketStore((s) => s.market);
  const qc = useQueryClient();
  const { data: watchlist } = useWatchlist();
  const { data: alerts } = useQuery({
    queryKey: ["alerts", market],
    queryFn: () => apiGet<AlertRow[]>("/alerts", {}, market),
  });

  const [symbol, setSymbol] = useState("");
  const [kind, setKind] = useState("price_above");
  const [threshold, setThreshold] = useState("");

  const create = useMutation({
    mutationFn: () => apiRequest("/alerts", {
        method: "POST",
        body: {
          market: market.toUpperCase(),
          symbol,
          kind,
          threshold: Number(threshold),
        },
      }),
    onSuccess: () => {
      setThreshold("");
      qc.invalidateQueries({ queryKey: ["alerts", market] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => apiRequest(`/alerts/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts", market] }),
  });

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (symbol && threshold) create.mutate();
        }}
        className="flex flex-wrap items-center gap-2 text-sm"
      >
        <select
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="rounded-lg border border-neutral-300 bg-transparent px-2 py-1.5 dark:border-neutral-700 dark:bg-neutral-900"
        >
          <option value="">選擇標的…</option>
          {watchlist?.map((w) => (
            <option key={w.symbol} value={w.symbol}>{w.symbol} {w.name}</option>
          ))}
        </select>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          className="rounded-lg border border-neutral-300 bg-transparent px-2 py-1.5 dark:border-neutral-700 dark:bg-neutral-900"
        >
          {KIND_OPTS.map((k) => (
            <option key={k.key} value={k.key}>{k.label}</option>
          ))}
        </select>
        <input
          type="number"
          step="any"
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
          placeholder="門檻值"
          className="w-28 rounded-lg border border-neutral-300 bg-transparent px-2 py-1.5 dark:border-neutral-700 dark:bg-neutral-900"
        />
        <button
          type="submit"
          disabled={!symbol || !threshold || create.isPending}
          className="rounded-lg bg-neutral-900 px-3 py-1.5 text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
        >
          ＋新增警示
        </button>
      </form>
      {create.isError && <p className="text-sm text-red-500">{(create.error as Error).message}</p>}

      {!alerts?.length ? (
        <p className="text-sm text-neutral-500">尚無警示規則。每日收盤資料更新後自動檢查。</p>
      ) : (
        <ul className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
          {alerts.map((a) => (
            <li key={a.id} className="flex items-center justify-between px-4 py-2 text-sm">
              <div>
                <span className="font-mono font-semibold">{a.symbol}</span>
                <span className="ml-2 text-neutral-500">
                  {a.kind_label} {a.threshold}
                  {a.kind.startsWith("premium") ? "%" : ""}
                </span>
                {a.last_triggered && (
                  <span className="ml-3 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700 dark:bg-amber-900 dark:text-amber-200">
                    🔔 {a.last_triggered.date} 觸發（{a.last_triggered.value}）
                  </span>
                )}
              </div>
              <button
                onClick={() => remove.mutate(a.id)}
                disabled={remove.isPending}
                className="text-xs text-neutral-400 hover:text-red-500 disabled:opacity-40"
              >
                刪除
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
