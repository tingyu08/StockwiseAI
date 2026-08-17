/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { afterEach, beforeEach, expect, it, describe, vi } from "vitest";

import type { OverviewData } from "@/hooks/use-groups";

const overview = vi.fn();
const runMutate = vi.fn();
vi.mock("@/hooks/use-groups", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useOverview: () => overview(),
  useRunOverview: () => ({ mutate: runMutate, isPending: false, isError: false }),
}));

const { OverviewCard } = await import("./overview-card");

const note = (symbol: string, yesterday: string) => ({
  symbol,
  yesterday,
  technical: "多頭",
  action: "持有" as const,
  rationale: "測試",
  entry_price: 10,
  stop_loss: 9,
  target_price: 12,
});

const data = (
  notes: ReturnType<typeof note>[],
  facts?: OverviewData["stock_facts"],
): OverviewData =>
  ({
    market: "TW",
    trade_date: "2026-08-14",
    model: "test",
    created_at: "2026-08-14T01:00:00Z",
    stock_facts: facts,
    report: {
      global_market: {
        index_comments: ["費半 +2.49%", "那斯達克 +1.1%"],
        key_stocks_comment: "台積電 ADR +1.68%",
        one_liner: "風險偏好回升",
        risk_sentiment: "risk_neutral",
      },
      local_market: {
        support: 44572,
        resistance: 46216,
        levels_rationale: "季線與近 20 日高點",
        flow_comment: "外資淨空單增加",
        prediction: "開高走高",
        prediction_rationales: ["費半領漲", "加權站上 MA20"],
      },
      stock_notes: notes,
      risks: {
        events: ["FOMC 會議紀要"],
        black_swan_watch: ["地緣政治"],
        monitor_signals: ["量能是否萎縮"],
      },
      overall_stance: "neutral",
    },
  }) as OverviewData;

const show = (d: OverviewData) => {
  overview.mockReturnValue({ data: d, isLoading: false, error: null });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(createElement(QueryClientProvider, { client }, createElement(OverviewCard)));
};

const rowOf = (symbol: string) => screen.getByText(symbol).closest("tr")!;

beforeEach(() => {
  overview.mockReset();
  runMutate.mockReset();
});
afterEach(cleanup);

describe("四個模組都要渲染", () => {
  // 原本只驗了模組 3，其餘三個模組的內容完全沒被斷言過
  it("全球盤勢、大盤預判、風險提示的內容都出現在畫面上", () => {
    show(data([note("00403A", "x")], {
      "00403A": { name: "測試", close: 10, change_pct: 1 },
    }));

    // 模組 1
    expect(screen.getByText("費半 +2.49%")).toBeInTheDocument();
    expect(screen.getByText(/風險偏好回升/)).toBeInTheDocument();
    // 模組 2
    expect(screen.getByText("費半領漲")).toBeInTheDocument();
    expect(screen.getByText("開高走高")).toBeInTheDocument();
    // 模組 4
    expect(screen.getByText("FOMC 會議紀要")).toBeInTheDocument();
    expect(screen.getByText("地緣政治")).toBeInTheDocument();
    expect(screen.getByText("量能是否萎縮")).toBeInTheDocument();
  });
});

describe("產生簡報", () => {
  it("按下按鈕會觸發重新產生", () => {
    show(data([note("00403A", "x")]));

    fireEvent.click(screen.getByRole("button", { name: "產生今日簡報" }));

    expect(runMutate).toHaveBeenCalledTimes(1);
  });
});

describe("核心標的點評", () => {
  it("顯示公司名稱，不是只有代號", () => {
    show(data([note("00403A", "收盤 10.3（+1.98%）")], {
      "00403A": { name: "主動統一升級50", close: 10.3, change_pct: 1.98 },
    }));

    expect(within(rowOf("00403A")).getByText("主動統一升級50")).toBeInTheDocument();
  });

  it("收盤價與漲跌同格呈現，表頭寫單位", () => {
    show(data([note("00403A", "收盤 10.3（+1.98%）")], {
      "00403A": { name: "主動統一升級50", close: 16.61, change_pct: 4.47 },
    }));

    expect(screen.getByText("昨日收盤")).toBeInTheDocument();
    // 同一格「16.61（+4.47%）」，不拆成兩欄；列內也不再重複「收盤」二字
    const row = rowOf("00403A");
    expect(row).toHaveTextContent("16.61（+4.47%）");
    expect(row).not.toHaveTextContent("收盤");
  });

  it("漲紅、跌綠、平盤黃", () => {
    show(data(
      [note("UP", "x"), note("DOWN", "x"), note("FLAT", "x")],
      {
        UP: { name: "漲", close: 10, change_pct: 1.98 },
        DOWN: { name: "跌", close: 10, change_pct: -2.5 },
        FLAT: { name: "平", close: 10, change_pct: 0 },
      },
    ));

    // 只有括號內的漲跌上色，價格本身保持中性
    expect(within(rowOf("UP")).getByText(/\+1\.98%/)).toHaveClass("text-red-500");
    expect(within(rowOf("DOWN")).getByText(/-2\.50%/)).toHaveClass("text-green-500");
    expect(within(rowOf("FLAT")).getByText(/0\.00%/)).toHaveClass("text-amber-500");
  });

  it("漲跌未知時只顯示價格，不可畫成平盤", () => {
    // 只有一天歷史 → change_pct 為 null。塗成黃色 0% 等於謊報「今天沒漲跌」
    show(data([note("NEW", "x")], {
      NEW: { name: "新上市", close: 10, change_pct: null },
    }));

    const row = rowOf("NEW");
    expect(row).toHaveTextContent("10");
    expect(row).not.toHaveTextContent("%");
  });

  it("AI 把名稱寫進 symbol 時仍對得上 stock_facts", () => {
    // 正式環境實際回傳過 symbol="00407A 主動凱基台灣"，與 facts 的純代號
    // key 對不起來，整張表因此退回顯示 AI 的原字串、也沒有顏色
    show(data([note("00407A 主動凱基台灣", "收盤 9.83（+1.76%）")], {
      "00407A": { name: "主動凱基台灣", close: 9.83, change_pct: 1.76 },
    }));

    const row = rowOf("00407A");
    expect(row).toHaveTextContent("9.83（+1.76%）");
    expect(row).not.toHaveTextContent("收盤");
    expect(within(row).getByText(/\+1\.76%/)).toHaveClass("text-red-500");
  });

  it("symbol 帶市場前綴時也對得上", () => {
    show(data([note("TW/2330", "x")], {
      "2330": { name: "台積電", close: 2395, change_pct: -1.64 },
    }));

    const row = rowOf("2330");
    expect(row).toHaveTextContent("2395（-1.64%）");
    expect(within(row).getByText("台積電")).toBeInTheDocument();
  });

  it("舊簡報沒有 stock_facts 時退回顯示原字串", () => {
    show(data([note("00403A", "收盤 10.3（+1.98%）")]));

    expect(within(rowOf("00403A")).getByText("收盤 10.3（+1.98%）")).toBeInTheDocument();
  });
});
