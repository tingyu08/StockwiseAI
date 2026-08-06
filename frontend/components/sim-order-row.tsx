"use client";

import { useState } from "react";

import type { SimOrderView } from "@/hooks/use-simulation";
import { useMarketStore } from "@/stores/market";

const STATUS_LABEL = { pending: "待成交", filled: "已成交", rejected: "已拒絕" } as const;

const money = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });
const signed = (v: number) => `${v >= 0 ? "+" : ""}${money(v)}`;

function Detail({
  label,
  value,
  cls = "",
  strong = false,
}: {
  label: string;
  value: string;
  cls?: string;
  strong?: boolean;
}) {
  return (
    <div>
      <span className="text-neutral-400">{label}</span>
      <span className={`ml-1.5 font-mono ${strong ? "font-semibold" : ""} ${cls}`}>{value}</span>
    </div>
  );
}

export function SimOrderRow({ order }: { order: SimOrderView }) {
  const [open, setOpen] = useState(false);
  const market = useMarketStore((s) => s.market);
  // 漲跌色隨市場慣例：台股紅漲綠跌，美股相反（與持倉表一致）
  const upCls = market === "tw" ? "text-red-500" : "text-green-500";
  const downCls = market === "tw" ? "text-green-500" : "text-red-500";
  const pnlCls = (order.realized_pnl ?? 0) >= 0 ? upCls : downCls;
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
        {order.fill_kind && (
          <span
            className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-700 dark:bg-amber-900 dark:text-amber-200"
            title="盤中哨兵以觸發當下的觀察價成交（非隔日開盤價）"
          >
            {order.fill_kind === "stop_loss" ? "⚡盤中停損" : "⚡盤中停利"}
          </span>
        )}
        <span className="ml-auto text-neutral-500">
          {order.qty.toLocaleString()} 股
          {order.fill_price != null && `｜成交 ${money(order.fill_price)}`}
        </span>
        {order.realized_pnl != null && (
          <span className={`text-xs font-semibold ${pnlCls}`} title="賣出淨額扣掉成本後的已實現損益">
            {signed(order.realized_pnl)}
            {order.realized_pnl_pct != null && `（${signed(order.realized_pnl_pct)}%）`}
          </span>
        )}
        <span
          className={`text-xs ${
            order.status === "rejected"
              ? "text-red-500"
              : order.status === "pending"
                ? "text-amber-500"
                : "text-neutral-400"
          }`}
        >
          {STATUS_LABEL[order.status]}
        </span>
        <span className="text-xs text-neutral-400">{order.ai_report ? "▾ AI 理由" : ""}</span>
      </button>
      {open && (
        <div className="mt-2 rounded-lg bg-neutral-50 p-3 text-xs leading-relaxed text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
          {order.reject_reason && <p className="mb-1 text-red-500">拒絕原因：{order.reject_reason}</p>}
          {order.gross_amount != null && order.fill_price != null && (
            <div className="mb-2 grid grid-cols-2 gap-x-4 gap-y-1 border-b border-neutral-200 pb-2 sm:grid-cols-3 dark:border-neutral-800">
              <Detail label="成交價" value={money(order.fill_price)} />
              <Detail label="股數" value={order.qty.toLocaleString()} />
              <Detail label="成交金額" value={money(order.gross_amount)} />
              <Detail
                label={order.side === "buy" ? "手續費" : "費用與稅"}
                value={order.fee != null ? money(order.fee) : "—"}
              />
              {order.net_amount != null && (
                <Detail
                  label={order.side === "buy" ? "實際支出" : "實際入袋"}
                  value={money(order.net_amount)}
                  strong
                />
              )}
              {order.avg_cost != null && <Detail label="成本均價" value={money(order.avg_cost)} />}
              {order.realized_pnl != null && (
                <Detail
                  label="已實現損益"
                  value={`${signed(order.realized_pnl)}${
                    order.realized_pnl_pct != null ? `（${signed(order.realized_pnl_pct)}%）` : ""
                  }`}
                  cls={pnlCls}
                  strong
                />
              )}
            </div>
          )}
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
          </p>
        </div>
      )}
    </li>
  );
}
