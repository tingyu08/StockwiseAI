/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { apiRequest, trackActiveJob } from "@/lib/api";
import { useMarketStore } from "@/stores/market";
import { useAddWatch } from "./use-stocks";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn(),
  apiRequest: vi.fn(),
  trackActiveJob: vi.fn(),
}));

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
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useAddWatch(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("2434");
    });

    expect(trackActiveJob).toHaveBeenCalledWith({
      runId: 44,
      name: "sync-tw-2434",
    });
  });
});
