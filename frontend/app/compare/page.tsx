"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import { useGroups } from "@/hooks/use-groups";
import { useWatchlist } from "@/hooks/use-stocks";
import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

const LINE_COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#a855f7", "#06b6d4", "#ec4899", "#84cc16"];

interface CompareData {
  metrics: {
    symbol: string;
    name: string;
    kind: string;
    return_1w: number | null;
    return_1m: number | null;
    return_3m: number | null;
    return_ytd: number | null;
    annualized_return: number | null;
    volatility: number | null;
    last_close: number;
  }[];
  series: Record<string, { date: string; value: number }[]>;
}

const COLUMNS = [
  { key: "return_1w", label: "1 週" },
  { key: "return_1m", label: "1 月" },
  { key: "return_3m", label: "3 月" },
  { key: "return_ytd", label: "YTD" },
  { key: "annualized_return", label: "年化" },
  { key: "volatility", label: "波動率" },
] as const;

export default function ComparePage() {
  const market = useMarketStore((s) => s.market);
  const { data: watchlist } = useWatchlist();
  const { data: groups } = useGroups();
  const [selected, setSelected] = useState<string[]>([]);
  const [range, setRange] = useState<"3m" | "6m" | "1y">("1y");
  const [sortKey, setSortKey] = useState<(typeof COLUMNS)[number]["key"]>("return_1m");

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ["compare", market, selected.join(","), range],
    queryFn: () =>
      apiGet<CompareData>("/compare", { symbols: selected.join(","), range }, market),
    enabled: selected.length >= 1,
  });

  const toggle = (symbol: string) =>
    setSelected((prev) =>
      prev.includes(symbol)
        ? prev.filter((s) => s !== symbol)
        : prev.length < 8
          ? [...prev, symbol]
          : prev,
    );

  const chartData = data ? mergeSeries(data.series) : [];
  const sortedMetrics = data
    ? [...data.metrics].sort((a, b) => (b[sortKey] ?? -Infinity) - (a[sortKey] ?? -Infinity))
    : [];

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">選擇比較標的（最多 8 檔）</h2>
          <div className="flex gap-1">
            {(["3m", "6m", "1y"] as const).map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={`rounded-md px-3 py-1 text-sm ${
                  range === r
                    ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                    : "text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
                }`}
              >
                {r === "3m" ? "3 個月" : r === "6m" ? "6 個月" : "1 年"}
              </button>
            ))}
          </div>
        </div>
        {!watchlist?.length && (
          <p className="text-sm text-neutral-500">自選清單為空，請先到儀表板加入股票。</p>
        )}
        {(() => {
          const sorted = [...(watchlist ?? [])].sort((a, b) => a.sort_order - b.sort_order);
          const sections = [
            ...(groups ?? []).map((g) => ({
              key: `g${g.id}`,
              name: g.name,
              items: sorted.filter((w) => w.group_id === g.id),
            })),
            { key: "none", name: "未分組", items: sorted.filter((w) => w.group_id === null) },
          ].filter((s) => s.items.length > 0);

          return sections.map((section) => {
            const allSelected = section.items.every((w) => selected.includes(w.symbol));
            return (
              <div key={section.key} className="mb-2">
                <div className="mb-1 flex items-center gap-2">
                  <h3 className="text-xs font-medium text-neutral-500">{section.name}</h3>
                  <button
                    onClick={() =>
                      setSelected((prev) => {
                        const symbols = section.items.map((w) => w.symbol);
                        if (allSelected) return prev.filter((s) => !symbols.includes(s));
                        const merged = [...prev, ...symbols.filter((s) => !prev.includes(s))];
                        return merged.slice(0, 8);
                      })
                    }
                    className="text-xs text-blue-500 hover:underline"
                  >
                    {allSelected ? "取消全選" : "全選此群組"}
                  </button>
                </div>
                <div className="flex flex-wrap gap-2">
                  {section.items.map((w) => (
                    <button
                      key={w.symbol}
                      onClick={() => toggle(w.symbol)}
                      className={`rounded-full border px-3 py-1 text-sm ${
                        selected.includes(w.symbol)
                          ? "border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                          : "border-neutral-300 text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                      }`}
                    >
                      {w.symbol} {w.name}
                    </button>
                  ))}
                </div>
              </div>
            );
          });
        })()}
        <FreshnessNote>{FRESHNESS.compare}</FreshnessNote>
      </section>

      {isError && <p className="text-sm text-red-500">{(error as Error).message}</p>}
      {isFetching && <p className="text-sm text-neutral-500">計算中…</p>}

      {data && (
        <>
          <section className="overflow-x-auto rounded-xl border border-neutral-200 dark:border-neutral-800">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800">
                  <th className="px-4 py-2">標的</th>
                  <th className="px-4 py-2 text-right">收盤</th>
                  {COLUMNS.map((c) => (
                    <th
                      key={c.key}
                      onClick={() => setSortKey(c.key)}
                      className={`cursor-pointer px-4 py-2 text-right hover:text-neutral-900 dark:hover:text-white ${
                        sortKey === c.key ? "font-semibold text-neutral-900 dark:text-white" : ""
                      }`}
                    >
                      {c.label}
                      {sortKey === c.key && " ↓"}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedMetrics.map((m) => (
                  <tr key={m.symbol} className="border-b border-neutral-100 last:border-0 dark:border-neutral-800/50">
                    <td className="px-4 py-2">
                      <span className="font-mono font-semibold">{m.symbol}</span>
                      <span className="ml-2 text-neutral-500">{m.name}</span>
                    </td>
                    <td className="px-4 py-2 text-right">{m.last_close.toLocaleString()}</td>
                    {COLUMNS.map((c) => (
                      <td key={c.key} className={`px-4 py-2 text-right ${pctColor(m[c.key], c.key, market)}`}>
                        {m[c.key] != null ? `${m[c.key]!.toFixed(1)}%` : "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
            <h3 className="mb-3 text-sm font-medium text-neutral-500">
              正規化報酬走勢（區間首日 = 100）
            </h3>
            <ResponsiveContainer width="100%" height={360}>
              <LineChart data={chartData}>
                <CartesianGrid strokeOpacity={0.15} vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} domain={["auto", "auto"]} width={48} />
                <Tooltip
                  contentStyle={{ fontSize: 12 }}
                  formatter={(v) => (typeof v === "number" ? v.toFixed(1) : String(v))}
                />
                <Legend />
                {selected.map((symbol, i) => (
                  <Line
                    key={symbol}
                    dataKey={symbol}
                    stroke={LINE_COLORS[i % LINE_COLORS.length]}
                    dot={false}
                    strokeWidth={1.6}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </section>
        </>
      )}
    </div>
  );
}

function mergeSeries(series: Record<string, { date: string; value: number }[]>) {
  const byDate = new Map<string, Record<string, number | string>>();
  for (const [symbol, points] of Object.entries(series)) {
    for (const p of points) {
      const row = byDate.get(p.date) ?? { date: p.date };
      row[symbol] = p.value;
      byDate.set(p.date, row);
    }
  }
  return [...byDate.values()].sort((a, b) =>
    String(a.date).localeCompare(String(b.date)),
  );
}

function pctColor(value: number | null, key: string, market: string): string {
  if (value == null || key === "volatility") return "";
  const positive = value > 0;
  // 台股紅漲綠跌；美股綠漲紅跌
  const upCls = market === "tw" ? "text-red-500" : "text-green-500";
  const downCls = market === "tw" ? "text-green-500" : "text-red-500";
  return positive ? upCls : value < 0 ? downCls : "";
}
