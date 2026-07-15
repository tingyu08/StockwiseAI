/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

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
  CandlestickChart: ({
    data,
    prediction,
  }: {
    data: unknown[];
    prediction?: unknown[];
  }) => <div>candles:{data.length};predictions:{prediction?.length ?? 0}</div>,
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
  dashboardMock.mockReset();
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

afterEach(cleanup);

it("renders all stock sections from one dashboard result", async () => {
  await act(async () => {
    render(<StockPage params={Promise.resolve({ symbol: "2330" })} />);
  });
  expect(await screen.findByText("TSMC")).toBeInTheDocument();
  expect(screen.getByText("candles:1;predictions:0")).toBeInTheDocument();
  expect(screen.getByText("indicators:1")).toBeInTheDocument();
  expect(screen.getByText("analysis:stored analysis")).toBeInTheDocument();
  expect(screen.getByText("news:stored news")).toBeInTheDocument();
  expect(dashboardMock).toHaveBeenCalledWith("2330", "1y");
});

it("shows request errors while the dashboard is loading", async () => {
  dashboardMock.mockReturnValue({
    isLoading: true,
    isError: true,
    error: new Error("dashboard unavailable"),
    data: undefined,
  });

  await act(async () => {
    render(<StockPage params={Promise.resolve({ symbol: "2330" })} />);
  });

  expect(screen.getByText("dashboard unavailable")).toBeInTheDocument();
});

it("switches ranges and hides prediction bands", async () => {
  dashboardMock.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: {
      stock: {
        symbol: "2330", market: "TW", name: "TSMC", currency: "TWD", kind: "stock", tracked: true,
      },
      series: [{ date: "2026-07-15", close: 100, rsi14: 50, kd_k: 40, kd_d: 30 }],
      prediction: { horizons: { "20": [{ date: "2026-07-16", mid: 101 }] } },
      analysis: null,
      news: null,
      usage: [],
    },
  });

  await act(async () => {
    render(<StockPage params={Promise.resolve({ symbol: "2330" })} />);
  });

  expect(screen.getByText("candles:1;predictions:1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByText("candles:1;predictions:0")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button")[0]);
  expect(dashboardMock).toHaveBeenLastCalledWith("2330", "3m");
});
