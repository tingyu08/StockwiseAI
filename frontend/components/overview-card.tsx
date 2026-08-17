"use client";

import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import {
  useOverview,
  useRunOverview,
  type DailyBriefing,
  type StockFact,
} from "@/hooks/use-groups";
import { formatRunTime } from "@/lib/datetime";
import { ApiError } from "@/lib/api";
import { useMarketStore } from "@/stores/market";

const STANCE = {
  bullish: { label: "偏多", cls: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200" },
  neutral: { label: "中性", cls: "bg-neutral-200 text-neutral-700 dark:bg-neutral-700 dark:text-neutral-200" },
  bearish: { label: "偏空", cls: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200" },
} as const;

const SENTIMENT = {
  risk_on: "風險偏好",
  risk_neutral: "風險中性",
  risk_off: "風險規避",
} as const;

const ACTION_CLS: Record<string, string> = {
  買進: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200",
  持有: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-200",
  減碼: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-200",
  觀望: "bg-neutral-200 text-neutral-700 dark:bg-neutral-700 dark:text-neutral-200",
};

export function OverviewCard() {
  const market = useMarketStore((s) => s.market);
  const { data, isLoading, error } = useOverview();
  const run = useRunOverview();
  const noData = error instanceof ApiError && error.status === 404;

  return (
    <section className="rounded-xl border border-neutral-200 p-6 dark:border-neutral-800">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">🧠 AI 每日投資簡報</h2>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
          title="自動分析全部自選股（有快取），並抓取全球指數與大盤數據產生四模組簡報"
        >
          {run.isPending ? "產生簡報中（約 30~60 秒）…" : "產生今日簡報"}
        </button>
      </div>

      {isLoading && <p className="text-sm text-neutral-500">載入中…</p>}
      {noData && !data && (
        <p className="text-sm text-neutral-500">
          尚無今日簡報。點右上按鈕：AI 會分析所有自選股＋全球指數與{market === "tw" ? "加權指數" : "S&P 500"}數據，
          產出盤勢總結、大盤預判、標的點評與風險提示。
        </p>
      )}
      {run.isError && <p className="text-sm text-red-500">{(run.error as Error).message}</p>}

      {/* 顯示「產生時間」而非 trade_date：後者是簡報所根據的收盤日，
          並排在畫面上會讓人誤以為排程停在那一天 */}
      {data && (
        <BriefingBody
          briefing={data.report}
          facts={data.stock_facts}
          meta={`${formatRunTime(data.created_at) ?? "尚未執行"} 產生｜${data.model}`}
        />
      )}

      <FreshnessNote>{FRESHNESS.overview}</FreshnessNote>
    </section>
  );
}

function BriefingBody({
  briefing,
  facts,
  meta,
}: {
  briefing: DailyBriefing;
  facts?: Record<string, StockFact>;
  meta: string;
}) {
  const b = briefing;
  return (
    <div className="space-y-5 text-sm">
      <div className="flex items-center gap-3">
        <span className={`rounded-full px-3 py-1 font-semibold ${STANCE[b.overall_stance].cls}`}>
          整體{STANCE[b.overall_stance].label}
        </span>
        <span className="text-xs text-neutral-400">{meta}</span>
      </div>

      {/* 模組 1 */}
      <Module title="① 全球盤勢總結">
        <ul className="list-inside list-disc space-y-0.5">
          {b.global_market.index_comments.map((c) => <li key={c}>{c}</li>)}
        </ul>
        <p className="mt-1">{b.global_market.key_stocks_comment}</p>
        <p className="mt-1 font-medium">
          💡 {b.global_market.one_liner}
          <span className="ml-2 rounded bg-neutral-200 px-1.5 py-0.5 text-xs dark:bg-neutral-700">
            {SENTIMENT[b.global_market.risk_sentiment]}
          </span>
        </p>
      </Module>

      {/* 模組 2 */}
      <Module title="② 今日大盤預判">
        <p>
          支撐 <span className="font-mono font-semibold">{b.local_market.support.toLocaleString()}</span>
          ｜壓力 <span className="font-mono font-semibold">{b.local_market.resistance.toLocaleString()}</span>
          <span className="ml-2 text-xs text-neutral-500">（{b.local_market.levels_rationale}）</span>
        </p>
        <p className="mt-1 text-neutral-600 dark:text-neutral-300">{b.local_market.flow_comment}</p>
        <p className="mt-1">
          預判：<span className="rounded bg-blue-100 px-2 py-0.5 font-semibold text-blue-700 dark:bg-blue-900 dark:text-blue-200">{b.local_market.prediction}</span>
        </p>
        <ul className="mt-1 list-inside list-disc space-y-0.5 text-neutral-600 dark:text-neutral-300">
          {b.local_market.prediction_rationales.map((r) => <li key={r}>{r}</li>)}
        </ul>
      </Module>

      {/* 模組 3 */}
      <Module title="③ 核心標的點評">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800">
                {/* 欄寬策略：技術面是唯一的長文字，放到最後並以 w-full 吸收
                    剩餘寬度，前面的數字欄才不會被它推擠。中間五欄等寬
                    （min-w-28 足以容納「106.7（+1.43%）」這種最長內容；
                    用 min-w 而非 w 是因為 table auto layout 會把 width 當成
                    建議值、內容短就壓縮，min-width 才真的守得住），
                    視線掃下來是對齊的。
                    技術面刻意不設字數上限（見 StockNote.technical）：
                    長到需要換行就讓它換行，反正它在最後一欄，
                    換行不會影響前面各欄的對齊。 */}
                <th className="py-1.5 pr-3 whitespace-nowrap">標的</th>
                {/* 單位寫在表頭，每一列就不必重複「收盤」二字 */}
                <th className="min-w-28 py-1.5 pr-3 text-right whitespace-nowrap">昨日收盤</th>
                <th className="min-w-28 py-1.5 pr-3 text-right whitespace-nowrap">建議</th>
                <th className="min-w-28 py-1.5 pr-3 text-right whitespace-nowrap">進場</th>
                <th className="min-w-28 py-1.5 pr-3 text-right whitespace-nowrap">停損</th>
                <th className="min-w-28 py-1.5 pr-3 text-right whitespace-nowrap">目標</th>
                <th className="w-full py-1.5">技術面</th>
              </tr>
            </thead>
            <tbody>
              {b.stock_notes.map((n) => (
                <tr key={n.symbol} className="border-b border-neutral-100 align-top last:border-0 dark:border-neutral-800/50" title={n.rationale}>
                  <td className="py-1.5 pr-3 whitespace-nowrap">
                    <span className="font-mono font-semibold">{symbolKey(n.symbol)}</span>
                    {facts?.[symbolKey(n.symbol)]?.name && (
                      <span className="ml-1.5 text-neutral-500">
                        {facts[symbolKey(n.symbol)].name}
                      </span>
                    )}
                  </td>
                  <Yesterday fact={facts?.[symbolKey(n.symbol)]} fallback={n.yesterday} />
                  <td className="min-w-28 py-1.5 pr-3 text-right">
                    <span className={`whitespace-nowrap rounded px-1.5 py-0.5 font-semibold ${ACTION_CLS[n.action] ?? ""}`}>
                      {n.action}
                    </span>
                  </td>
                  <td className="min-w-28 py-1.5 pr-3 text-right font-mono whitespace-nowrap">{n.entry_price}</td>
                  <td className="min-w-28 py-1.5 pr-3 text-right font-mono whitespace-nowrap">{n.stop_loss}</td>
                  <td className="min-w-28 py-1.5 pr-3 text-right font-mono whitespace-nowrap">{n.target_price}</td>
                  <td className="w-full py-1.5">{n.technical}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-xs text-neutral-400">滑鼠停在列上可看理由。</p>
      </Module>

      {/* 模組 4 */}
      <Module title="④ 今日風險提示">
        {b.risks.events.length > 0 && (
          <div>
            <span className="text-xs font-medium text-neutral-500">重大事件／數據：</span>
            <ul className="list-inside list-disc space-y-0.5">
              {b.risks.events.map((e) => <li key={e}>{e}</li>)}
            </ul>
          </div>
        )}
        {b.risks.black_swan_watch.length > 0 && (
          <div className="mt-1">
            <span className="text-xs font-medium text-neutral-500">黑天鵝觀察：</span>
            <ul className="list-inside list-disc space-y-0.5">
              {b.risks.black_swan_watch.map((e) => <li key={e}>{e}</li>)}
            </ul>
          </div>
        )}
        {b.risks.monitor_signals.length > 0 && (
          <div className="mt-1">
            <span className="text-xs font-medium text-neutral-500">盤中監控訊號：</span>
            <ul className="list-inside list-disc space-y-0.5">
              {b.risks.monitor_signals.map((e) => <li key={e}>{e}</li>)}
            </ul>
          </div>
        )}
      </Module>

      <p className="text-xs text-neutral-400">
        指數與 ADR 為系統抓取的真實收盤數據；AI 解讀僅供參考，不構成投資建議。
      </p>
    </div>
  );
}

/** 取出純代號，用來對上 stock_facts 的 key。
 *
 *  AI 常把名稱一起寫進 symbol（實際回傳過「00407A 主動凱基台灣」），
 *  也可能加上市場前綴（「TW/2330」）。prompt 已明令只填代號，但那是
 *  請求而非保證，且既有簡報已經是這種格式——查找端一律正規化才穩。
 *  後端 gemini._normalize_symbol 對個股分析做的是同一件事。
 */
function symbolKey(raw: string): string {
  const text = raw.trim().toUpperCase();
  const afterSlash = text.includes("/") ? text.slice(text.lastIndexOf("/") + 1) : text;
  return afterSlash.split(/\s+/)[0] ?? afterSlash;
}

/** 漲跌色：漲紅、跌綠、平盤黃（台股慣例）。
 *
 *  這裡刻意不跟著市場切換（持倉表與交易日誌是美股綠漲紅跌）：簡報是給
 *  人「一眼掃過去」的表格，同一份畫面裡兩套配色比不一致更難讀。
 */
function changeClass(pct: number): string {
  if (pct > 0) return "text-red-500";
  if (pct < 0) return "text-green-500";
  return "text-amber-500";
}

/** 昨日收盤（含漲跌）。
 *
 *  價格與漲跌同格呈現為「16.61（+4.47%）」：兩者是同一件事的兩種說法，
 *  拆成兩欄會讓視線在掃描時多跳一次。只有括號內上色——價格本身沒有方向，
 *  整格著色反而讓表格顯得吵。
 *
 *  facts 缺漏時（本欄位上線前產生的舊簡報）退回顯示 AI 那串 yesterday，
 *  寧可少了顏色也不要空白。
 */
function Yesterday({ fact, fallback }: { fact?: StockFact; fallback: string }) {
  if (!fact || fact.close == null) {
    return (
      <td className="min-w-28 py-1.5 pr-3 whitespace-nowrap text-neutral-500">{fallback}</td>
    );
  }
  const pct = fact.change_pct;
  return (
    <td className="min-w-28 py-1.5 pr-3 text-right font-mono whitespace-nowrap">
      {fact.close}
      {pct != null && (
        <span className={changeClass(pct)}>
          （{pct > 0 ? "+" : ""}{pct.toFixed(2)}%）
        </span>
      )}
    </td>
  );
}

function Module({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-neutral-100 p-3 dark:border-neutral-800/70">
      <h3 className="mb-1.5 text-sm font-semibold">{title}</h3>
      {children}
    </div>
  );
}
