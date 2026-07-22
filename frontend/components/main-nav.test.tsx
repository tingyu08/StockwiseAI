/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { isPathAvailable, navItemsFor } from "@/lib/nav";
import { useMarketStore } from "@/stores/market";
import { MainNav } from "./main-nav";

describe("導覽的市場適用性", () => {
  beforeEach(() => {
    useMarketStore.setState({ market: "tw" });
  });

  afterEach(cleanup);

  it("台股顯示折溢價", () => {
    render(<MainNav />);
    expect(screen.getByText("折溢價")).toBeInTheDocument();
  });

  it("美股不顯示折溢價，其餘項目照舊", () => {
    useMarketStore.setState({ market: "us" });
    render(<MainNav />);

    expect(screen.queryByText("折溢價")).not.toBeInTheDocument();
    for (const label of ["儀表板", "比較", "模擬交易", "回測"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("navItemsFor 依市場過濾", () => {
    expect(navItemsFor("tw").map((i) => i.href)).toContain("/premium");
    expect(navItemsFor("us").map((i) => i.href)).not.toContain("/premium");
  });

  it("isPathAvailable 判斷切換市場後是否該導離", () => {
    expect(isPathAvailable("/premium", "tw")).toBe(true);
    expect(isPathAvailable("/premium", "us")).toBe(false);
    // 全市場皆有的頁面不受影響
    expect(isPathAvailable("/compare", "us")).toBe(true);
    expect(isPathAvailable("/", "us")).toBe(true);
  });
});
