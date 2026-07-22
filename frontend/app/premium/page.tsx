"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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
import { usePremiumHistory, usePremiumList, usePremiumSupported } from "@/hooks/use-premium";
import { TOOLTIP_CONTENT_STYLE, TOOLTIP_LABEL_STYLE } from "@/lib/chart-theme";
import { useMarketStore } from "@/stores/market";

export default function PremiumPage() {
  const market = useMarketStore((s) => s.market);
  const supported = usePremiumSupported();
  const router = useRouter();
  const { data: rows, isLoading, isError, error } = usePremiumList();
  const [selected, setSelected] = useState<string | null>(null);
  const { data: history } = usePremiumHistory(selected);

  // 此市場不提供折溢價（如美股）→ 導覽列本就不顯示本頁，
  // 直接輸入網址時導回儀表板，不留下空殼頁面
  useEffect(() => {
    if (!supported) router.replace(`/?market=${market}`);
  }, [supported, market, router]);

  if (!supported) return null;

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-neutral-200 dark:border-neutral-800">
        <div className="flex items-center justify-between px-5 pt-4">
          <h2 className="text-lg font-semibold">ETF 折溢價</h2>
          <p className="text-xs text-neutral-400">
            正=溢價（市價高於淨值）、負=折價
          </p>
        </div>
        <div className="px-5">
          <FreshnessNote>{FRESHNESS.premium}</FreshnessNote>
        </div>
        {isLoading && <p className="p-5 text-sm text-neutral-500">載入中…</p>}
        {isError && <p className="p-5 text-sm text-red-500">{(error as Error).message}</p>}
        {rows && rows.length === 0 && (
          <p className="p-5 text-sm text-neutral-500">自選清單中沒有台股 ETF。</p>
        )}
        {rows && rows.length > 0 && (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800">
                <th className="px-5 py-2">ETF</th>
                <th className="px-5 py-2 text-right">淨值</th>
                <th className="px-5 py-2 text-right">市價</th>
                <th className="px-5 py-2 text-right">折溢價</th>
                <th className="px-5 py-2 text-right">日期</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.symbol}
                  onClick={() => setSelected(r.symbol)}
                  className={`cursor-pointer border-b border-neutral-100 last:border-0 hover:bg-neutral-50 dark:border-neutral-800/50 dark:hover:bg-neutral-900 ${
                    selected === r.symbol ? "bg-neutral-50 dark:bg-neutral-900" : ""
                  }`}
                >
                  <td className="px-5 py-2">
                    <span className="font-mono font-semibold">{r.symbol}</span>
                    <span className="ml-2 text-neutral-500">{r.name}</span>
                  </td>
                  <td className="px-5 py-2 text-right">{r.nav ?? "不適用"}</td>
                  <td className="px-5 py-2 text-right">{r.close ?? "—"}</td>
                  <td
                    className={`px-5 py-2 text-right font-semibold ${premiumColor(r.premium_pct)}`}
                  >
                    {r.premium_pct != null ? `${r.premium_pct > 0 ? "+" : ""}${r.premium_pct.toFixed(2)}%` : "不適用"}
                  </td>
                  <td className="px-5 py-2 text-right text-neutral-400">{r.date ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {selected && history && history.length > 0 && (
        <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
          <h3 className="mb-3 text-sm font-medium text-neutral-500">
            {selected} 折溢價歷史（%）
          </h3>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={history}>
              <CartesianGrid strokeOpacity={0.15} vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 11 }} width={48} tickFormatter={(v) => `${v}%`} />
              <Tooltip
                contentStyle={TOOLTIP_CONTENT_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
                formatter={(v) => (typeof v === "number" ? `${v.toFixed(2)}%` : String(v))}
              />
              <ReferenceLine y={0} stroke="#737373" strokeDasharray="4 4" />
              <Line dataKey="premium_pct" stroke="#3b82f6" dot={false} strokeWidth={1.6} name="折溢價" />
            </LineChart>
          </ResponsiveContainer>
          {history.length < 5 && (
            <p className="mt-2 text-xs text-neutral-400">
              歷史資料由每日排程累積，天數越多曲線越完整。
            </p>
          )}
        </section>
      )}
    </div>
  );
}

function premiumColor(v: number | null): string {
  if (v == null) return "text-neutral-400";
  if (v > 0.5) return "text-red-500";
  if (v < -0.5) return "text-green-500";
  return "";
}
