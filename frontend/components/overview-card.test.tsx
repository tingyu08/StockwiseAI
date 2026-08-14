/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { afterEach, beforeEach, expect, it, describe, vi } from "vitest";

import type { OverviewData } from "@/hooks/use-groups";

const overview = vi.fn();
vi.mock("@/hooks/use-groups", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useOverview: () => overview(),
  useRunOverview: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
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
        index_comments: [], key_stocks_comment: "", one_liner: "",
        risk_sentiment: "risk_neutral",
      },
      local_market: {
        support: 1, resistance: 2, levels_rationale: "", flow_comment: "",
        prediction: "開高走高", prediction_rationales: [],
      },
      stock_notes: notes,
      risks: { events: [], black_swan_watch: [], monitor_signals: [] },
      overall_stance: "neutral",
    },
  }) as OverviewData;

const show = (d: OverviewData) => {
  overview.mockReturnValue({ data: d, isLoading: false, error: null });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(createElement(QueryClientProvider, { client }, createElement(OverviewCard)));
};

const rowOf = (symbol: string) => screen.getByText(symbol).closest("tr")!;

beforeEach(() => overview.mockReset());
afterEach(cleanup);

describe("核心標的點評", () => {
  it("顯示公司名稱，不是只有代號", () => {
    show(data([note("00403A", "收盤 10.3（+1.98%）")], {
      "00403A": { name: "主動統一升級50", close: 10.3, change_pct: 1.98 },
    }));

    expect(within(rowOf("00403A")).getByText("主動統一升級50")).toBeInTheDocument();
  });

  it("表頭寫單位，每一列不再重複「收盤」二字", () => {
    show(data([note("00403A", "收盤 10.3（+1.98%）")], {
      "00403A": { name: "主動統一升級50", close: 10.3, change_pct: 1.98 },
    }));

    expect(screen.getByText("昨日收盤")).toBeInTheDocument();
    expect(rowOf("00403A")).not.toHaveTextContent("收盤");
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

    expect(within(rowOf("UP")).getByText("+1.98%")).toHaveClass("text-red-500");
    expect(within(rowOf("DOWN")).getByText("-2.50%")).toHaveClass("text-green-500");
    expect(within(rowOf("FLAT")).getByText("0.00%")).toHaveClass("text-amber-500");
  });

  it("漲幅未知時顯示破折號，不可畫成平盤", () => {
    // 只有一天歷史 → change_pct 為 null。塗成黃色等於謊報「今天沒漲跌」
    show(data([note("NEW", "x")], {
      NEW: { name: "新上市", close: 10, change_pct: null },
    }));

    const dash = within(rowOf("NEW")).getByText("—");
    expect(dash).not.toHaveClass("text-amber-500");
  });

  it("舊簡報沒有 stock_facts 時退回顯示原字串", () => {
    show(data([note("00403A", "收盤 10.3（+1.98%）")]));

    expect(within(rowOf("00403A")).getByText("收盤 10.3（+1.98%）")).toBeInTheDocument();
  });
});
