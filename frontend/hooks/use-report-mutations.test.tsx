/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { apiRequest, waitForJob } from "@/lib/api";
import type { AnalysisData, NewsData, StockDashboard } from "@/lib/types";
import { useMarketStore } from "@/stores/market";
import { useRunRoutine } from "./use-analysis";
import { useRunNews } from "./use-news";

vi.mock("@/lib/api", () => ({
  apiRequest: vi.fn(),
  waitForJob: vi.fn(),
  trackActiveJob: vi.fn(),
  removeActiveJob: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const base = {
    stock: {
      symbol: "2330", market: "TW", name: "TSMC", currency: "TWD", kind: "stock", tracked: true,
    },
    series: [], prediction: null, analysis: null, news: null, usage: [],
  } as StockDashboard;
  queryClient.setQueryData(["stock-dashboard", "tw", "2330", "3m"], base);
  queryClient.setQueryData(["stock-dashboard", "tw", "2330", "1y"], base);
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { queryClient, wrapper };
}

beforeEach(() => {
  vi.clearAllMocks();
  useMarketStore.setState({ market: "tw" });
});

it("updates every cached dashboard range after routine analysis", async () => {
  const analysis = {
    trade_date: "2026-07-15", kind: "routine", model: "flash", report: {},
  } as AnalysisData;
  vi.mocked(apiRequest).mockResolvedValue(analysis);
  const { queryClient, wrapper } = setup();
  const { result } = renderHook(() => useRunRoutine("2330"), { wrapper });
  await act(async () => { await result.current.mutateAsync(); });
  expect(queryClient.getQueryData<StockDashboard>(
    ["stock-dashboard", "tw", "2330", "3m"],
  )?.analysis).toEqual(analysis);
  expect(queryClient.getQueryData<StockDashboard>(
    ["stock-dashboard", "tw", "2330", "1y"],
  )?.analysis).toEqual(analysis);
});

it("updates every cached dashboard range after news research", async () => {
  const news = {
    date: "2026-07-15", model: "antigravity", summary: "stored", created_at: null,
  } as NewsData;
  vi.mocked(apiRequest).mockResolvedValue({ started: true, job: "news-tw-2330", run_id: 8 });
  vi.mocked(waitForJob).mockResolvedValue(news);
  const { queryClient, wrapper } = setup();
  const { result } = renderHook(() => useRunNews("2330"), { wrapper });
  await act(async () => { await result.current.mutateAsync(); });
  expect(queryClient.getQueryData<StockDashboard>(
    ["stock-dashboard", "tw", "2330", "3m"],
  )?.news).toEqual(news);
  expect(queryClient.getQueryData<StockDashboard>(
    ["stock-dashboard", "tw", "2330", "1y"],
  )?.news).toEqual(news);
});
