import { beforeEach, describe, expect, it } from "vitest";

import { MARKET_CURRENCY, MARKET_LABELS, useMarketStore } from "./market";

beforeEach(() => useMarketStore.setState({ market: "tw" }));

describe("市場狀態", () => {
  it("預設為台股", () => {
    expect(useMarketStore.getState().market).toBe("tw");
  });

  it("setMarket 會更新全域市場", () => {
    // 所有帶 market 的 query key 靠這個值失效重抓，改不動等於整頁停在舊市場
    useMarketStore.getState().setMarket("us");

    expect(useMarketStore.getState().market).toBe("us");
  });

  it("兩個市場都有標籤與幣別", () => {
    // 缺一個會讓畫面出現 undefined，而不是明顯的錯誤
    for (const market of ["tw", "us"] as const) {
      expect(MARKET_LABELS[market]).toBeTruthy();
      expect(MARKET_CURRENCY[market]).toBeTruthy();
    }
    expect(MARKET_CURRENCY.tw).toBe("TWD");
    expect(MARKET_CURRENCY.us).toBe("USD");
  });
});
