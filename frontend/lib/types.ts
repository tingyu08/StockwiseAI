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

export interface Scenario {
  target_price: number;
  trigger_condition: string;
  probability: number;
}

export interface AnalysisData {
  trade_date: string;
  kind: "routine" | "deep" | "news";
  model: string;
  report: {
    symbol: string;
    action: "buy" | "sell" | "hold";
    confidence: number;
    target_price_low: number;
    target_price_high: number;
    stop_loss: number;
    reasoning: string;
    scenarios: { bull: Scenario; base: Scenario; bear: Scenario };
    risks: string[];
  };
  created_at: string | null;
}

export interface UsageRow {
  model: string;
  rpd: number;
  used: number;
  remaining: number;
}

export interface NewsData {
  date: string;
  model: string;
  summary: string;
  created_at: string | null;
}

export interface PredictionBandPoint {
  date: string;
  mid: number;
  upper: number;
  lower: number;
}

export interface PredictionData {
  trade_date: string;
  method: string;
  horizons: Record<string, PredictionBandPoint[]>;
  disclaimer: string;
}

export interface StockDashboard extends PriceSeries {
  prediction: PredictionData | null;
  analysis: AnalysisData | null;
  news: NewsData | null;
  usage: UsageRow[];
}
