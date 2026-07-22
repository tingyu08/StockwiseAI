"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import { useWatchlist } from "@/hooks/use-stocks";
import { apiGet, apiRequest } from "@/lib/api";
import { TOOLTIP_CONTENT_STYLE, TOOLTIP_LABEL_STYLE } from "@/lib/chart-theme";
import { useMarketStore } from "@/stores/market";

interface BacktestResult {
  strategy: string;
  strategy_desc: string;
  period: { start: string; end: string };
  metrics: {
    total_return_pct: number;
    annualized_pct: number | null;
    max_drawdown_pct: number;
    win_rate_pct: number | null;
    trades: number;
    buy_hold_return_pct: number;
    beats_buy_hold: number;
    sharpe_ratio: number | null;
  };
  assumptions: { slippage_bps: number };
  equity_curve: { date: string; equity: number }[];
  trades: {
    entry_date: string;
    entry_price: number;
    exit_date: string | null;
    exit_price: number | null;
    pnl_pct: number | null;
  }[];
  disclaimer: string;
}

/** 後端 range 為 0~200 bps（見 api/v1/backtest.py 的 Field 約束）。 */
function clampSlippage(value: number): number {
  return Math.min(200, Math.max(0, Math.round(value)));
}

const RANGE_OPTS = [
  { days: 365, label: "1 年" },
  { days: 730, label: "2 年" },
  { days: 1095, label: "3 年" },
];

export default function BacktestPage() {
  const market = useMarketStore((s) => s.market);
  const { data: watchlist } = useWatchlist();
  const { data: strategies } = useQuery({
    queryKey: ["bt-strategies"],
    queryFn: () => apiGet<{ key: string; desc: string }[]>("/backtest/strategies"),
  });

  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("ma_cross");
  const [rangeDays, setRangeDays] = useState(365);
  // 滑價是自由輸入，若直接進 queryKey 會變成「每按一鍵打一次完整回測」
  // （5→125 就是三次）。輸入文字與送出值分離，靜止 400ms 後才 commit。
  const [slippageInput, setSlippageInput] = useState("5");
  const [slippageBps, setSlippageBps] = useState(5);

  useEffect(() => {
    const timer = setTimeout(() => {
      const parsed = Number(slippageInput);
      if (slippageInput.trim() === "" || !Number.isFinite(parsed)) return;
      setSlippageBps(clampSlippage(parsed));
    }, 400);
    return () => clearTimeout(timer);
  }, [slippageInput]);

  // 失焦時把顯示值正規化（超範圍、空白、小數）
  const commitSlippage = (raw: string) => {
    const parsed = Number(raw);
    const next =
      raw.trim() === "" || !Number.isFinite(parsed) ? 0 : clampSlippage(parsed);
    setSlippageBps(next);
    setSlippageInput(String(next));
  };

  // 標的/策略/期間/滑價一變就自動重跑（結果為確定性計算，當 query 快取）
  const run = useQuery({
    queryKey: ["backtest", market, symbol, strategy, rangeDays, slippageBps],
    queryFn: () =>
      apiRequest<BacktestResult>("/backtest", {
        method: "POST",
        body: {
          market: market.toUpperCase(),
          symbol,
          strategy,
          range_days: rangeDays,
          slippage_bps: slippageBps,
        },
      }),
    enabled: !!symbol,
    staleTime: 5 * 60_000,
  });

  const result = run.data;

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
        <h2 className="mb-3 text-lg font-semibold">策略回測（規則策略，非 AI）</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-neutral-500">標的</span>
            <select
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="rounded-lg border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700 dark:bg-neutral-900"
            >
              <option value="">選擇自選股…</option>
              {watchlist?.map((w) => (
                <option key={w.symbol} value={w.symbol}>
                  {w.symbol} {w.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-neutral-500">滑價（bps）</span>
            <input
              type="number"
              min={0}
              max={200}
              value={slippageInput}
              onChange={(event) => setSlippageInput(event.target.value)}
              onBlur={(event) => commitSlippage(event.target.value)}
              className="w-24 rounded-lg border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700"
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-neutral-500">策略</span>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="rounded-lg border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700 dark:bg-neutral-900"
            >
              {strategies?.map((s) => (
                <option key={s.key} value={s.key}>{s.desc}</option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-neutral-500">期間</span>
            <select
              value={rangeDays}
              onChange={(e) => setRangeDays(Number(e.target.value))}
              className="rounded-lg border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700 dark:bg-neutral-900"
            >
              {RANGE_OPTS.map((r) => (
                <option key={r.days} value={r.days}>{r.label}</option>
              ))}
            </select>
          </label>
          {run.isFetching && (
            <span className="pb-2 text-sm text-neutral-500">回測中…</span>
          )}
        </div>
        {run.isError && <p className="mt-2 text-sm text-red-500">{(run.error as Error).message}</p>}
        <FreshnessNote>{FRESHNESS.backtest}</FreshnessNote>
      </section>

      {result && (
        <>
          <section className="grid grid-cols-2 gap-4 rounded-xl border border-neutral-200 p-5 sm:grid-cols-4 lg:grid-cols-7 dark:border-neutral-800">
            <Metric label="總報酬" value={`${result.metrics.total_return_pct}%`} highlight={result.metrics.total_return_pct >= 0} />
            <Metric label="年化" value={result.metrics.annualized_pct != null ? `${result.metrics.annualized_pct}%` : "—"} />
            <Metric label="最大回撤" value={`-${result.metrics.max_drawdown_pct}%`} negative />
            <Metric label="勝率" value={result.metrics.win_rate_pct != null ? `${result.metrics.win_rate_pct}%` : "—"} />
            <Metric label="Sharpe" value={result.metrics.sharpe_ratio?.toFixed(2) ?? "—"} />
            <Metric label="交易次數" value={String(result.metrics.trades)} />
            <Metric label="買入持有" value={`${result.metrics.buy_hold_return_pct}%`} />
            <Metric
              label="超額報酬"
              value={`${result.metrics.beats_buy_hold >= 0 ? "+" : ""}${result.metrics.beats_buy_hold}%`}
              highlight={result.metrics.beats_buy_hold >= 0}
            />
          </section>

          <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
            <h3 className="mb-3 text-sm font-medium text-neutral-500">
              策略權益曲線（起始資金 = 1.0）｜{result.period.start} ～ {result.period.end}
            </h3>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={result.equity_curve}>
                <CartesianGrid strokeOpacity={0.15} vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} width={56} domain={["auto", "auto"]} />
                <Tooltip contentStyle={TOOLTIP_CONTENT_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
                <ReferenceLine y={1} stroke="#737373" strokeDasharray="4 4" />
                <Line dataKey="equity" stroke="#3b82f6" dot={false} strokeWidth={1.8} name="策略權益" />
              </LineChart>
            </ResponsiveContainer>
          </section>

          <section className="overflow-x-auto rounded-xl border border-neutral-200 dark:border-neutral-800">
            <h3 className="px-5 pt-4 text-sm font-medium text-neutral-500">交易明細（最近 50 筆）</h3>
            <table className="mt-2 w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800">
                  <th className="px-5 py-2">進場</th>
                  <th className="px-5 py-2 text-right">進場價</th>
                  <th className="px-5 py-2">出場</th>
                  <th className="px-5 py-2 text-right">出場價</th>
                  <th className="px-5 py-2 text-right">損益</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.map((t, i) => (
                  <tr key={i} className="border-b border-neutral-100 last:border-0 dark:border-neutral-800/50">
                    <td className="px-5 py-2">{t.entry_date}</td>
                    <td className="px-5 py-2 text-right">{t.entry_price}</td>
                    <td className="px-5 py-2">{t.exit_date ?? "持有中"}</td>
                    <td className="px-5 py-2 text-right">{t.exit_price ?? "—"}</td>
                    <td className={`px-5 py-2 text-right font-semibold ${(t.pnl_pct ?? 0) >= 0 ? "text-red-500" : "text-green-500"}`}>
                      {t.pnl_pct != null ? `${t.pnl_pct >= 0 ? "+" : ""}${t.pnl_pct}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <p className="text-xs text-neutral-400">{result.disclaimer}。</p>
        </>
      )}
    </div>
  );
}

function Metric({ label, value, highlight, negative }: {
  label: string; value: string; highlight?: boolean; negative?: boolean;
}) {
  const cls = negative ? "text-amber-500" : highlight === undefined ? "" : highlight ? "text-red-500" : "text-green-500";
  return (
    <div>
      <div className="text-xs text-neutral-500">{label}</div>
      <div className={`text-lg font-semibold ${cls}`}>{value}</div>
    </div>
  );
}
