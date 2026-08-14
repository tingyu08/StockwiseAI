/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";
import { useGroups, useOverview } from "./use-groups";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn(),
  apiRequest: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

let wrapper: ({ children }: PropsWithChildren) => React.JSX.Element;

beforeEach(() => {
  vi.clearAllMocks();
  useMarketStore.setState({ market: "tw" });
  // 每個測試一個 client，但 wrapper 本身不可每次呼叫就重建——那會讓
  // hook 掛到不同 client 上而重複發出請求
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
});

describe("useOverview", () => {
  it("帶著目前市場去取簡報", async () => {
    vi.mocked(apiGet).mockResolvedValue({ market: "TW" });

    const { result } = renderHook(() => useOverview(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiGet).toHaveBeenCalledWith("/analysis/overview", {}, "tw");
  });

  it("切到美股時會用美股重抓", async () => {
    // 市場沒進 query key 的話，切換後畫面會停在上一個市場的簡報
    vi.mocked(apiGet).mockResolvedValue({ market: "US" });
    useMarketStore.setState({ market: "us" });

    const { result } = renderHook(() => useOverview(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiGet).toHaveBeenCalledWith("/analysis/overview", {}, "us");
  });

  it("沒有當日簡報時不重試", async () => {
    // 404 是「今天還沒產生」的正常狀態，重試只是白打後端。
    // 以 failureCount 判定而非呼叫次數：renderHook 的重新渲染也會計入
    // 呼叫次數，那不是重試。
    vi.mocked(apiGet).mockRejectedValue(new Error("not found"));

    const { result } = renderHook(() => useOverview(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.failureCount).toBe(1);
  });
});

describe("useGroups", () => {
  it("依市場取得分組清單", async () => {
    vi.mocked(apiGet).mockResolvedValue([]);

    const { result } = renderHook(() => useGroups(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiGet).toHaveBeenCalledWith("/groups", {}, "tw");
  });
});
