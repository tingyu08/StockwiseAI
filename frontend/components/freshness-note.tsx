/** 數據更新頻率的簡短說明（統一樣式）。 */
export function FreshnessNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-1 text-xs text-neutral-400" title="數據更新頻率">
      ⏱ {children}
    </p>
  );
}

export const FRESHNESS = {
  prices: "日線資料：每日收盤後自動更新（台股約 14:30、美股約台灣時間 05:30）",
  analysis: "AI 分析：每日收盤後自動批次（AI 託管股）；其餘股票手動觸發，同一交易日只分析一次",
  premium: "折溢價：每日收盤後快照一次（台股 14:45／美股 05:45），歷史逐日累積",
  simulation: "模擬交易：委託於次一交易日開盤價成交；帳戶數據隨每日同步更新",
  compare: "報酬率：依日線資料計算，每日收盤後更新",
  backtest: "回測：使用截至最近收盤日的歷史日線",
  overview: "AI 總評：每交易日一份（快取），點按鈕即可產生今日總評",
} as const;
