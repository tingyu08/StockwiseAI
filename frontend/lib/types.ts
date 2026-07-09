export interface StockInfo {
  symbol: string;
  market: "TW" | "US";
  name: string;
  currency: string;
  kind: "stock" | "etf";
  tracked: boolean;
}

export interface PricePoint {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  rsi14: number | null;
  kd_k: number | null;
  kd_d: number | null;
  macd: number | null;
  macd_signal: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}

export interface PriceSeries {
  stock: StockInfo;
  series: PricePoint[];
}

export interface WatchItem {
  symbol: string;
  name: string;
  market: "TW" | "US";
  kind: "stock" | "etf";
  ai_managed: boolean;
  group_id: number | null;
  sort_order: number;
}
