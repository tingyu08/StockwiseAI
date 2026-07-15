/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";
import { DASHBOARD_STALE_MS, useStockDashboard } from "./use-dashboard";

vi.mock("@/lib/api", () => ({ apiGet: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  useMarketStore.setState({ market: "tw" });
});

it("loads the complete stock page with one dashboard request", async () => {
  vi.mocked(apiGet).mockResolvedValue({
    stock: {
      symbol: "2330",
      market: "TW",
      name: "TSMC",
      currency: "TWD",
      kind: "stock",
      tracked: true,
    },
    series: [],
    prediction: null,
    analysis: null,
    news: null,
    usage: [],
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  renderHook(() => useStockDashboard("2330", "1y"), { wrapper });

  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
  expect(apiGet).toHaveBeenCalledWith(
    "/stocks/2330/dashboard",
    { range: "1y" },
    "tw",
  );
  const query = queryClient.getQueryCache().find({
    queryKey: ["stock-dashboard", "tw", "2330", "1y"],
  });
  expect(query?.options.staleTime).toBe(DASHBOARD_STALE_MS);
});

it("separates market and range in the dashboard cache key", async () => {
  vi.mocked(apiGet).mockResolvedValue({
    stock: {}, series: [], prediction: null, analysis: null, news: null, usage: [],
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const { rerender } = renderHook(
    ({ range }) => useStockDashboard("AAPL", range),
    { initialProps: { range: "3m" }, wrapper },
  );
  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
  rerender({ range: "1y" });
  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
  expect(queryClient.getQueryData(["stock-dashboard", "tw", "AAPL", "3m"])).toBeDefined();
  expect(queryClient.getQueryData(["stock-dashboard", "tw", "AAPL", "1y"])).toBeDefined();
});
