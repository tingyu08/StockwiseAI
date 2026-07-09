"use client";

import { useState } from "react";
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

import { AiManagedPanel } from "@/components/ai-managed-panel";
import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import {
  useRunSimStep,
  useSimAccount,
  useSimOrders,
  type SimOrderView,
} from "@/hooks/use-simulation";
import { useMarketStore } from "@/stores/market";

export default function SimulationPage() {
  const market = useMarketStore((s) => s.market);
  const { data: account, isLoading } = useSimAccount();
  const { data: orders } = useSimOrders();
  const runStep = useRunSimStep();

  const pnlPositive = (account?.total_pnl ?? 0) >= 0;
  const upCls = market === "tw" ? "text-red-500" : "text-green-500";
  const downCls = market === "tw" ? "text-green-500" : "text-red-500";

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">🤖 AI 模擬交易帳戶</h2>
          <div className="flex gap-2">
            <button
              onClick={() => runStep.mutate("decide")}
              disabled={runStep.isPending}
              className="rounded-md border border-neutral-300 px-3 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
              title="依最新 AI 報告產生委託單（正式流程由每日排程執行）"
            >
              手動觸發決策
            </button>
            <button
              onClick={() => runStep.mutate("fill")}
              disabled={runStep.isPending}
              className="rounded-md border border-neutral-300 px-3 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
              title="以次一交易日開盤價撮合 pending 單"
            >
              手動撮合
            </button>
          </div>
        </div>

        {isLoading && <p className="text-sm text-neutral-500">載入中…</p>}
        {runStep.isError && (
          <p className="mb-2 text-sm text-red-500">{(runStep.error as Error).message}</p>
        )}
        {account && (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="總權益" value={`${account.equity.toLocaleString()} ${account.currency}`} />
            <Stat label="現金" value={account.cash.toLocaleString()} />
            <Stat
              label="總損益"
              value={`${account.total_pnl >= 0 ? "+" : ""}${account.total_pnl.toLocaleString()}`}
              cls={pnlPositive ? upCls : downCls}
            />
            <Stat
              label="報酬率"
              value={`${account.total_pnl_pct >= 0 ? "+" : ""}${account.total_pnl_pct}%`}
              cls={pnlPositive ? upCls : downCls}
            />
          </div>
        )}
      </section>

      <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
        <h3 className="mb-2 text-sm font-medium text-neutral-500">⚙️ AI 託管管理</h3>
        <AiManagedPanel />
      </section>

      {account && account.equity_curve.length > 1 && (
        <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
          <h3 className="mb-3 text-sm font-medium text-neutral-500">權益曲線</h3>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={account.equity_curve}>
              <CartesianGrid strokeOpacity={0.15} vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
              <YAxis
                tick={{ fontSize: 11 }}
                width={80}
                domain={["auto", "auto"]}
                tickFormatter={(v) => Number(v).toLocaleString()}
              />
              <Tooltip
                contentStyle={{ fontSize: 12 }}
                formatter={(v) => (typeof v === "number" ? v.toLocaleString() : String(v))}
              />
              <ReferenceLine y={account.initial_cash} stroke="#737373" strokeDasharray="4 4" />
              <Line dataKey="equity" stroke="#3b82f6" dot={false} strokeWidth={1.8} name="權益" />
            </LineChart>
          </ResponsiveContainer>
        </section>
      )}

      {account && (
        <section className="overflow-x-auto rounded-xl border border-neutral-200 dark:border-neutral-800">
          <h3 className="px-5 pt-4 text-sm font-medium text-neutral-500">持倉</h3>
          {account.positions.length === 0 ? (
            <p className="p-5 text-sm text-neutral-500">
              目前無持倉。到儀表板的自選清單開啟「AI 託管」，AI 會在每日分析後自動下單。
            </p>
          ) : (
            <table className="mt-2 w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800">
                  <th className="px-5 py-2">標的</th>
                  <th className="px-5 py-2 text-right">股數</th>
                  <th className="px-5 py-2 text-right">均價</th>
                  <th className="px-5 py-2 text-right">現價</th>
                  <th className="px-5 py-2 text-right">市值</th>
                  <th className="px-5 py-2 text-right">未實現損益</th>
                </tr>
              </thead>
              <tbody>
                {account.positions.map((p) => (
                  <tr key={p.symbol} className="border-b border-neutral-100 last:border-0 dark:border-neutral-800/50">
                    <td className="px-5 py-2">
                      <span className="font-mono font-semibold">{p.symbol}</span>
                      <span className="ml-2 text-neutral-500">{p.name}</span>
                    </td>
                    <td className="px-5 py-2 text-right">{p.qty.toLocaleString()}</td>
                    <td className="px-5 py-2 text-right">{p.avg_cost ?? "—"}</td>
                    <td className="px-5 py-2 text-right">{p.close ?? "—"}</td>
                    <td className="px-5 py-2 text-right">{p.market_value?.toLocaleString() ?? "—"}</td>
                    <td
                      className={`px-5 py-2 text-right font-semibold ${
                        (p.unrealized_pnl ?? 0) >= 0 ? upCls : downCls
                      }`}
                    >
                      {p.unrealized_pnl != null
                        ? `${p.unrealized_pnl >= 0 ? "+" : ""}${p.unrealized_pnl.toLocaleString()}（${p.unrealized_pnl_pct}%）`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      <section className="rounded-xl border border-neutral-200 dark:border-neutral-800">
        <h3 className="px-5 pt-4 text-sm font-medium text-neutral-500">交易日誌</h3>
        {!orders?.length ? (
          <p className="p-5 text-sm text-neutral-500">尚無交易紀錄。</p>
        ) : (
          <ul className="mt-2 divide-y divide-neutral-100 dark:divide-neutral-800/50">
            {orders.map((o) => (
              <OrderRow key={o.id} order={o} />
            ))}
          </ul>
        )}
      </section>

      <p className="text-xs text-neutral-400">
        模擬交易使用虛擬資金，隔日開盤價成交（台股含手續費與證交稅）。僅供研究參考，不構成投資建議。
      </p>
      <FreshnessNote>{FRESHNESS.simulation}</FreshnessNote>
    </div>
  );
}

function Stat({ label, value, cls = "" }: { label: string; value: string; cls?: string }) {
  return (
    <div>
      <div className="text-xs text-neutral-500">{label}</div>
      <div className={`text-xl font-semibold ${cls}`}>{value}</div>
    </div>
  );
}

const STATUS_LABEL = { pending: "待成交", filled: "已成交", rejected: "已拒絕" } as const;

function OrderRow({ order }: { order: SimOrderView }) {
  const [open, setOpen] = useState(false);
  const sideCls =
    order.side === "buy"
      ? "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200"
      : "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200";

  return (
    <li className="px-5 py-3">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 text-left text-sm">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold ${sideCls}`}>
          {order.side === "buy" ? "買" : "賣"}
        </span>
        <span className="font-mono font-semibold">{order.symbol}</span>
        <span className="text-neutral-500">{order.name}</span>
        <span className="ml-auto text-neutral-500">
          {order.qty.toLocaleString()} 股
          {order.fill_price != null && ` @ ${order.fill_price}`}
        </span>
        <span
          className={`text-xs ${
            order.status === "rejected" ? "text-red-500" : order.status === "pending" ? "text-amber-500" : "text-neutral-400"
          }`}
        >
          {STATUS_LABEL[order.status]}
        </span>
        <span className="text-xs text-neutral-400">{order.ai_report ? "▾ AI 理由" : ""}</span>
      </button>
      {open && (
        <div className="mt-2 rounded-lg bg-neutral-50 p-3 text-xs leading-relaxed text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
          {order.reject_reason && <p className="mb-1 text-red-500">拒絕原因：{order.reject_reason}</p>}
          {order.ai_report ? (
            <>
              <p className="mb-1">
                AI 判斷：{order.ai_report.action}（信心 {(order.ai_report.confidence * 100).toFixed(0)}%）
                ｜停損 {order.ai_report.stop_loss}
              </p>
              <p>{order.ai_report.reasoning}</p>
            </>
          ) : (
            <p>停損觸發或無報告連結。</p>
          )}
          <p className="mt-1 text-neutral-400">
            建立 {order.created_at?.slice(0, 16).replace("T", " ")}
            {order.filled_at && `｜成交 ${order.filled_at.slice(0, 10)}`}
            {order.fee != null && `｜費用 ${order.fee}`}
          </p>
        </div>
      )}
    </li>
  );
}
