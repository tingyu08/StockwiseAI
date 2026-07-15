/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";
import { useAnalysis, useUsage } from "./use-analysis";
import { useNews } from "./use-news";
import { usePredictions } from "./use-premium";
import { useWatchlist } from "./use-stocks";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiGet).mockResolvedValue([]);
  useMarketStore.setState({ market: "tw" });
});

it("uses query-specific cache durations", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  renderHook(() => {
    useWatchlist();
    useAnalysis("2330");
    useNews("2330");
    usePredictions("2330");
    useUsage();
  }, { wrapper });

  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(5));
  expect(queryClient.getQueryCache().find({ queryKey: ["watchlist", "tw"] })?.options.staleTime)
    .toBe(10 * 60_000);
  expect(queryClient.getQueryCache().find({ queryKey: ["analysis", "tw", "2330"] })?.options.staleTime)
    .toBe(10 * 60_000);
  expect(queryClient.getQueryCache().find({ queryKey: ["news", "tw", "2330"] })?.options.staleTime)
    .toBe(10 * 60_000);
  expect(queryClient.getQueryCache().find({ queryKey: ["predictions", "tw", "2330"] })?.options.staleTime)
    .toBe(5 * 60_000);
  expect(queryClient.getQueryCache().find({ queryKey: ["usage"] })?.options.staleTime)
    .toBe(60_000);
});
