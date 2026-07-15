/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { act, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import StockPage from "./page";

const dashboardMock = vi.fn();
vi.mock("@/hooks/use-dashboard", () => ({
  useStockDashboard: (...args: unknown[]) => dashboardMock(...args),
}));
vi.mock("@/stores/market", () => ({
  useMarketStore: (selector: (state: { market: string }) => unknown) =>
    selector({ market: "tw" }),
}));
vi.mock("@/components/charts/candlestick", () => ({
  CandlestickChart: ({ data }: { data: unknown[] }) => <div>candles:{data.length}</div>,
}));
vi.mock("@/components/charts/technical-indicators", () => ({
  TechnicalIndicatorsChart: ({ data }: { data: unknown[] }) => (
    <div>indicators:{data.length}</div>
  ),
}));
vi.mock("@/components/analysis/report-card", () => ({
  ReportCard: ({ data }: { data: { report: { reasoning: string } } | null }) => (
    <div>analysis:{data?.report.reasoning}</div>
  ),
}));
vi.mock("@/components/analysis/news-card", () => ({
  NewsCard: ({ data }: { data: { summary: string } | null }) => (
    <div>news:{data?.summary}</div>
  ),
}));

beforeEach(() => {
  dashboardMock.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: {
      stock: {
        symbol: "2330",
        market: "TW",
        name: "TSMC",
        currency: "TWD",
        kind: "stock",
        tracked: true,
      },
      series: [{ date: "2026-07-15", close: 100 }],
      prediction: null,
      analysis: { report: { reasoning: "stored analysis" } },
      news: { summary: "stored news" },
      usage: [],
    },
  });
});

it("renders all stock sections from one dashboard result", async () => {
  await act(async () => {
    render(<StockPage params={Promise.resolve({ symbol: "2330" })} />);
  });
  expect(await screen.findByText("TSMC")).toBeInTheDocument();
  expect(screen.getByText("candles:1")).toBeInTheDocument();
  expect(screen.getByText("indicators:1")).toBeInTheDocument();
  expect(screen.getByText("analysis:stored analysis")).toBeInTheDocument();
  expect(screen.getByText("news:stored news")).toBeInTheDocument();
  expect(dashboardMock).toHaveBeenCalledWith("2330", "1y");
});
