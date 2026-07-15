/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiGet, apiRequest, trackActiveJob } from "@/lib/api";
import { useMarketStore } from "@/stores/market";
import {
  useAddWatch,
  usePrices,
  useRemoveWatch,
  useSearch,
  useWatchlist,
} from "./use-stocks";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn(),
  apiRequest: vi.fn(),
  trackActiveJob: vi.fn(),
}));

function createQueryWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { queryClient, wrapper };
}

beforeEach(() => {
  vi.clearAllMocks();
  useMarketStore.setState({ market: "tw" });
});

describe("stock queries", () => {
  it("loads prices for the selected market and requested range", async () => {
    vi.mocked(apiGet).mockResolvedValue({ symbol: "2330", market: "TW", prices: [] });
    const { wrapper } = createQueryWrapper();

    renderHook(() => usePrices("2330", "1m"), { wrapper });

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith(
        "/stocks/2330/prices",
        { range: "1m" },
        "tw",
      ),
    );
  });

  it("does not search for blank text and searches non-blank text", async () => {
    vi.mocked(apiGet).mockResolvedValue([]);
    const { wrapper } = createQueryWrapper();
    const { rerender } = renderHook(
      ({ query }) => useSearch(query),
      { initialProps: { query: "   " }, wrapper },
    );

    expect(apiGet).not.toHaveBeenCalled();
    rerender({ query: "TSMC" });

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith("/stocks", { q: "TSMC" }, "tw"),
    );
  });

  it("loads the watchlist for the selected market", async () => {
    vi.mocked(apiGet).mockResolvedValue([]);
    const { wrapper } = createQueryWrapper();

    renderHook(() => useWatchlist(), { wrapper });

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith("/watchlist", {}, "tw"),
    );
  });
});

describe("useAddWatch", () => {
  it("tracks the durable stock sync job", async () => {
    useMarketStore.setState({ market: "tw" });
    vi.mocked(apiRequest).mockResolvedValue({
      symbol: "2434",
      market: "TW",
      name: "Example",
      started: true,
      job: "sync-tw-2434",
      run_id: 44,
    });
    const { queryClient, wrapper } = createQueryWrapper();
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useAddWatch(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("2434");
    });

    expect(trackActiveJob).toHaveBeenCalledWith({
      runId: 44,
      name: "sync-tw-2434",
    });
    expect(apiRequest).toHaveBeenCalledWith("/watchlist", {
      method: "POST",
      body: { market: "TW", symbol: "2434" },
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["watchlist", "tw"],
    });
  });

  it("does not track an add response without a durable job", async () => {
    vi.mocked(apiRequest).mockResolvedValue({
      symbol: "2330",
      market: "TW",
      name: "TSMC",
      started: false,
      job: null,
      run_id: null,
    });
    const { wrapper } = createQueryWrapper();
    const { result } = renderHook(() => useAddWatch(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync("2330");
    });

    expect(trackActiveJob).not.toHaveBeenCalled();
  });
});

describe("useRemoveWatch", () => {
  it("deletes the symbol and invalidates its market watchlist", async () => {
    vi.mocked(apiRequest).mockResolvedValue(undefined);
    useMarketStore.setState({ market: "us" });
    const { queryClient, wrapper } = createQueryWrapper();
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useRemoveWatch(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync("AAPL");
    });

    expect(apiRequest).toHaveBeenCalledWith("/watchlist/AAPL", {
      method: "DELETE",
      market: "us",
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["watchlist", "us"],
    });
  });
});
