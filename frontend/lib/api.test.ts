import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";

const okResponse = (data: unknown) => ({
  ok: true,
  status: 200,
  json: async () => ({ success: true, data, error: null, meta: null }),
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("API client", () => {
  it("adds the session bearer token to requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("window", {
      sessionStorage: { getItem: () => "single-user-secret" },
    });

    await api.apiGet("/usage");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer single-user-secret",
        }),
      }),
    );
  });

  it("provides one typed request function for JSON mutations", async () => {
    const request = (api as typeof api & {
      apiRequest?: <T>(path: string, options: unknown) => Promise<T>;
    }).apiRequest;
    expect(request).toBeTypeOf("function");
    if (!request) return;

    const fetchMock = vi.fn().mockResolvedValue(okResponse({ updated: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    await request<{ updated: number }>("/watchlist/reorder", {
      method: "PUT",
      body: { market: "TW", items: [] },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ market: "TW", items: [] }),
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
  });

  it("polls a background job until its result is ready", async () => {
    const waitForJob = (api as typeof api & {
      waitForJob?: <T>(runId: number, options?: { intervalMs?: number }) => Promise<T>;
    }).waitForJob;
    expect(waitForJob).toBeTypeOf("function");
    if (!waitForJob) return;

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ status: "running", result: null }))
      .mockResolvedValueOnce(
        okResponse({ status: "succeeded", result: { summary: "完成" } }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(waitForJob<{ summary: string }>(42, { intervalMs: 0 })).resolves.toEqual({
      summary: "完成",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("turns an HTML gateway error into a readable ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        headers: { get: () => "text/html" },
        text: async () => "<html>Bad gateway</html>",
      }),
    );

    await expect(api.apiGet("/usage")).rejects.toMatchObject({
      name: "Error",
      status: 502,
      message: "服務暫時無法回應（HTTP 502），請稍後重試",
    });
  });

  it("does not start polling when the job signal is already aborted", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();

    await expect(
      api.waitForJob(42, { intervalMs: 0, signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aborts requests after the configured timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_url, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
      ),
    );

    const rejection = expect(api.apiRequest("/usage", { timeoutMs: 25 })).rejects.toMatchObject({
      status: 408,
    });
    await vi.advanceTimersByTimeAsync(25);
    await rejection;
  });

  it("exposes Retry-After on rate-limit errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        headers: { get: (name: string) => name.toLowerCase() === "retry-after" ? "12" : "application/json" },
        json: async () => ({ success: false, data: null, error: "配額已用完", meta: null }),
      }),
    );

    await expect(api.apiGet("/usage")).rejects.toMatchObject({
      status: 429,
      retryAfterSeconds: 12,
    });
  });

  it("persists active jobs so polling can resume after navigation", () => {
    const extended = api as typeof api & {
      trackActiveJob?: (job: { runId: number; name: string }) => void;
      listActiveJobs?: () => { runId: number; name: string }[];
      removeActiveJob?: (runId: number) => void;
    };
    expect(extended.trackActiveJob).toBeTypeOf("function");
    expect(extended.listActiveJobs).toBeTypeOf("function");
    expect(extended.removeActiveJob).toBeTypeOf("function");
    if (!extended.trackActiveJob || !extended.listActiveJobs || !extended.removeActiveJob) return;

    const values = new Map<string, string>();
    vi.stubGlobal("window", {
      sessionStorage: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
      },
    });

    extended.trackActiveJob({ runId: 7, name: "overview-tw" });
    expect(extended.listActiveJobs()).toEqual([
      expect.objectContaining({ runId: 7, name: "overview-tw" }),
    ]);
    extended.removeActiveJob(7);
    expect(extended.listActiveJobs()).toEqual([]);
  });
});
