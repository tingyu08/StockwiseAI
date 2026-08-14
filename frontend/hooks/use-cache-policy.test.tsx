/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";
import { useAnalysis } from "./use-analysis";
import { useNews } from "./use-news";
import { usePredictions } from "./use-premium";
import { staleTimeOf } from "./query-test-utils";
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
  }, { wrapper });

  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(4));
  expect(staleTimeOf(queryClient, ["watchlist", "tw"])).toBe(10 * 60_000);
  expect(staleTimeOf(queryClient, ["analysis", "tw", "2330"])).toBe(10 * 60_000);
  expect(staleTimeOf(queryClient, ["news", "tw", "2330"])).toBe(10 * 60_000);
  expect(staleTimeOf(queryClient, ["predictions", "tw", "2330"])).toBe(5 * 60_000);
});
