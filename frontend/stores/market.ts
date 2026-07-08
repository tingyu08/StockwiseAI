import { create } from "zustand";

export type Market = "tw" | "us";

interface MarketState {
  market: Market;
  setMarket: (market: Market) => void;
}

/**
 * 全域市場狀態。切換時所有帶 market 的 TanStack Query key 自動失效重抓。
 * URL 同步（?market=）由 MarketSwitch 元件處理。
 */
export const useMarketStore = create<MarketState>((set) => ({
  market: "tw",
  setMarket: (market) => set({ market }),
}));

export const MARKET_LABELS: Record<Market, string> = {
  tw: "🇹🇼 台股",
  us: "🇺🇸 美股",
};

export const MARKET_CURRENCY: Record<Market, string> = {
  tw: "TWD",
  us: "USD",
};
