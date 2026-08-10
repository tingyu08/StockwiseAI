/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, expect, it, describe } from "vitest";

import type { SimOrderView } from "@/hooks/use-simulation";

import { SimOrderRow } from "./sim-order-row";

const base: SimOrderView = {
  id: 1,
  symbol: "3037",
  name: "欣興",
  side: "sell",
  qty: 202,
  fill_price: 988,
  fee: 883.12,
  gross_amount: 199576,
  net_amount: 198692.88,
  avg_cost: 900.5,
  realized_pnl: 16791.88,
  realized_pnl_pct: 9.23,
  status: "filled",
  decided_by: "ai",
  fill_kind: "take_profit",
  reject_reason: null,
  created_at: "2026-08-05T01:10:00",
  filled_at: "2026-08-05T00:00:00",
  ai_report: null,
};

const row = (order: Partial<SimOrderView>) =>
  render(createElement(SimOrderRow, { order: { ...base, ...order } }));

afterEach(cleanup); // vitest 未開 globals，RTL 不會自動清 DOM

describe("交易日誌列", () => {
  it("成交價講白話，不用 @ 符號", () => {
    row({});

    expect(screen.getByText(/202 股｜成交 988/)).toBeInTheDocument();
    // 「@」對非交易背景的讀者無意義，是這次改版要拿掉的東西
    expect(screen.queryByText(/@/)).not.toBeInTheDocument();
  });

  it("賣出在收合狀態就看得到已實現損益與百分比", () => {
    row({});

    expect(screen.getByText(/\+16,791\.88/)).toBeInTheDocument();
    expect(screen.getByText(/\+9\.23%/)).toBeInTheDocument();
  });

  it("展開後列出賣出的金額拆解與成本均價", () => {
    row({});
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("成交金額").nextSibling).toHaveTextContent("199,576");
    // 賣出扣的是手續費＋證交稅，標籤不可只寫「手續費」
    expect(screen.getByText("費用與稅").nextSibling).toHaveTextContent("883.12");
    expect(screen.getByText("實際入袋").nextSibling).toHaveTextContent("198,692.88");
    expect(screen.getByText("成本均價").nextSibling).toHaveTextContent("900.5");
  });

  it("買進顯示實際支出，且沒有已實現損益", () => {
    row({
      side: "buy",
      symbol: "0050",
      qty: 270,
      fill_price: 55.2,
      fee: 21.24,
      gross_amount: 14904,
      net_amount: 14925.24,
      avg_cost: null,
      realized_pnl: null,
      realized_pnl_pct: null,
      fill_kind: null,
    });
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("實際支出").nextSibling).toHaveTextContent("14,925.24");
    expect(screen.getByText("手續費").nextSibling).toHaveTextContent("21.24");
    // 買進只是把現金換成部位，此刻沒有實現損益
    expect(screen.queryByText("已實現損益")).not.toBeInTheDocument();
  });

  it("虧損顯示負號", () => {
    row({ realized_pnl: -1060, realized_pnl_pct: -10.58 });

    expect(screen.getByText(/-1,060/)).toBeInTheDocument();
    expect(screen.getByText(/-10\.58%/)).toBeInTheDocument();
  });

  it("成交時刻以市場當地時區顯示，並標明是哪個市場的時間", () => {
    // 台股 01:00Z ＝ 台北 09:00（開盤）
    row({ created_at: "2026-08-04T23:10:00Z", filled_at: "2026-08-05T01:00:00Z" });
    fireEvent.click(screen.getByRole("button"));

    const line = screen.getByText(/建立/);
    expect(line).toHaveTextContent("成交 2026-08-05 09:00");
    // 沒有標註的話，讀者無從判斷這是自己的時區還是市場的
    expect(line).toHaveTextContent("台北時間");
    // 舊版只顯示日期、且直接切字串（等同顯示 UTC）
    expect(line).not.toHaveTextContent("01:00");
  });

  it("未成交不顯示任何金額——填 0 會被讀成不用錢", () => {
    row({
      status: "pending",
      fill_price: null,
      fee: null,
      gross_amount: null,
      net_amount: null,
      avg_cost: null,
      realized_pnl: null,
      realized_pnl_pct: null,
      fill_kind: null,
      filled_at: null,
    });
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText(/202 股/)).toBeInTheDocument();
    expect(screen.queryByText(/成交 /)).not.toBeInTheDocument();
    expect(screen.queryByText("成交金額")).not.toBeInTheDocument();
  });
});
